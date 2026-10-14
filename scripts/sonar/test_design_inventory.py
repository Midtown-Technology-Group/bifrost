"""Inventory regeneration must be complete and preserve reviewed evidence."""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("design_inventory_under_test", ROOT / "scripts/design-inventory.py")
inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory
SPEC.loader.exec_module(inventory)


class InventoryEvidence(unittest.TestCase):
    def generate(self, root, output):
        with patch.object(sys, "argv", ["design-inventory", "--root", str(root), "--out-dir", str(output)]), \
                patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(inventory.main(), 0)
        return json.loads((output / "inventory.json").read_text())

    def test_real_tree_inventory_and_regeneration_preserve_reviewed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = self.generate(ROOT, output)
            self.assertGreater(first["counts"]["routes"], 40)
            self.assertTrue(any(row["full_path"] == "/solutions" for row in first["routes"]))
            for key in ("pages", "shared_ui_primitives", "feature_components"):
                expected = first["counts"][key]
                self.assertEqual(len(first[key]), expected)
                self.assertFalse(any(row["path"].endswith(".test.tsx") for row in first[key]))
                first[key][0]["rendered_proof"] = "reviewed-private-receipt"
                first[key][0]["status"] = "Reviewed"
            first["routes"][0].update(status="Reviewed", evidence="receipt|with\nseparator")
            first["families"][0].update(status="Reviewed", rendered_proof="family-receipt")
            (output / "inventory.json").write_text(json.dumps(first))
            second = self.generate(ROOT, output)
            self.assertEqual(first["counts"], second["counts"])
            for key in ("pages", "shared_ui_primitives", "feature_components"):
                self.assertEqual(second[key][0]["rendered_proof"], "reviewed-private-receipt")
                self.assertEqual(second[key][0]["status"], "Reviewed")
            self.assertEqual(second["routes"][0]["evidence"], "receipt|with\nseparator")
            self.assertEqual(second["families"][0]["rendered_proof"], "family-receipt")
            markdown = (output / "coverage.md").read_text()
            self.assertIn("receipt\\|with<br>separator", markdown)
            self.assertIn("/solutions", markdown)
            self.assertIn("Required Families", markdown)

    def test_fixture_discovers_nested_pages_and_excludes_test_or_ui_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                "client/src/App.tsx": '<Route path="/" element={<Layout/>}><Route index element={<Home/>}/><Route path="settings" element={<ProtectedRoute requirePlatformAdmin><Settings/></ProtectedRoute>} /></Route>',
                "client/src/pages/Home.tsx": "export function Home() {}",
                "client/src/pages/Home.test.tsx": "test-only",
                "client/src/pages/settings/Security.tsx": "export function Security() {}",
                "client/src/pages/settings/components/Panel.tsx": "export function Panel() {}",
                "client/src/components/ui/button.tsx": "export function Button() {}",
                "client/src/components/ui/button.test.tsx": "test-only",
                "client/src/components/editor/Panel.tsx": "export function Panel() {}",
                "client/src/components/editor/Panel.test.tsx": "test-only",
            }
            for path, content in paths.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            result = self.generate(root, root / "results")
            self.assertEqual(result["counts"]["pages"], 3)
            self.assertEqual(result["counts"]["shared_ui_primitives"], 1)
            self.assertEqual(result["counts"]["feature_components"], 1)
            route = next(row for row in result["routes"] if row["full_path"] == "/settings")
            self.assertEqual(route["access"], "platform admin")
            self.assertEqual(route["page_component"], "Settings")
            self.assertEqual(route["layout"], "Layout")

    def test_parser_handles_nested_braces_strings_and_loader_without_executing_js(self):
        source = '''<Route path="/outer/" element={<Layout/>}>
          <Route path="child" loader={() => ({value: "x>y", nested: {quote: 'a\\\'b'}})}
            errorElement={<Error/>} element={<ProtectedRoute requireOrgUser><Child/></ProtectedRoute>} />
          <Route path="*" element={<Missing/>} />
        </Route>'''
        routes = inventory._flatten_routes(inventory._route_nodes(source))
        child = next(row for row in routes if row.full_path == "/outer/child")
        self.assertEqual(child.page_component, "Child")
        self.assertEqual(child.access, "org user")
        self.assertTrue(child.has_loader)
        self.assertTrue(child.has_error_boundary)
        self.assertTrue(routes[-1].is_wildcard)
        self.assertEqual(inventory._join_path("/outer", "/absolute", False), "/absolute")

    def test_incomplete_route_tags_fail_instead_of_claiming_complete_inventory(self):
        for source in ('<Route path="broken"', '<Route path="a"></Route'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                inventory._route_nodes(source)
        self.assertIsNone(inventory._extract_braced_attr('<Route element="plain"/>', "element"))
        self.assertIsNone(inventory._extract_braced_attr('<Route element={unterminated', "element"))
        self.assertIsNone(inventory._component_name_from_route_tag('<Route path="empty"/>'))
        self.assertEqual(inventory._escape_cell(None), "Pending")

    def test_component_map_retains_lazy_aliases_and_static_layout_imports(self):
        source = '''import { Layout, ProtectedRoute as Guard } from "./layout";
        const Home = lazyWithReload(() => import("./pages/Home").then((m) => ({ default: m.Home })));
        export const loadSettings = () => import("./pages/Settings").then(m => m);
        const Settings = lazyWithReload(loadSettings);
        const Unknown = lazyWithReload(notDeclared);'''
        mapping = inventory._collect_lazy_component_map(source)
        self.assertEqual(mapping["Layout"], "./layout")
        self.assertEqual(mapping["Guard"], "./layout")
        self.assertEqual(mapping["Home"], "./pages/Home")
        self.assertEqual(mapping["Settings"], "./pages/Settings")
        self.assertNotIn("Unknown", mapping)

    def test_corrupt_prior_inventory_is_not_treated_as_reviewed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inventory.json").write_text("incomplete {")
            self.assertEqual(inventory._load_existing_inventory(root), {})
        current = [{"path": "new.tsx", "status": "Pending"}, {"path": "kept.tsx", "status": "Pending"}]
        existing = [{"path": "kept.tsx", "status": "Reviewed"}, {"path": "deleted.tsx", "status": "Reviewed"}]
        merged = inventory._merge_existing_rows(current, existing, ("path",))
        self.assertEqual([row["path"] for row in merged], ["new.tsx", "kept.tsx"])
        self.assertEqual(merged[0]["status"], "Pending")
        self.assertEqual(merged[1]["status"], "Reviewed")
        self.assertEqual(current[1]["status"], "Pending")


if __name__ == "__main__":
    unittest.main()
