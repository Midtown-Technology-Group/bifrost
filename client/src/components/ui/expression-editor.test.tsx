import { useEffect } from "react";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { OnMount } from "@monaco-editor/react";
import { ExpressionEditor } from "./expression-editor";

const fixture = vi.hoisted(() => ({
	editor: { onDidBlurEditorText: vi.fn() },
	monaco: {
		languages: {},
		typescript: {
			ScriptTarget: { ES2020: 7 },
			javascriptDefaults: {
				setDiagnosticsOptions: vi.fn(),
				setCompilerOptions: vi.fn(),
				addExtraLib: vi.fn(),
			},
		},
	},
}));
vi.mock("@/hooks/useBifrostMonacoTheme", () => ({
	useBifrostMonacoTheme: () => ({ onMount: vi.fn(), options: {} }),
}));
vi.mock("@monaco-editor/react", () => ({
	default: function Editor({ onMount }: { onMount: OnMount }) {
		useEffect(() => {
			onMount(
				fixture.editor as unknown as Parameters<OnMount>[0],
				fixture.monaco as unknown as Parameters<OnMount>[1],
			);
		}, [onMount]);
		return null;
	},
}));

describe("ExpressionEditor", () => {
	it("mounts with the installed editor's public TypeScript API and supplies context completions", () => {
		render(<ExpressionEditor value="context.field.enabled" onChange={vi.fn()} />);
		const defaults = fixture.monaco.typescript.javascriptDefaults;
		expect(defaults.setDiagnosticsOptions).toHaveBeenCalledWith({
			noSemanticValidation: false,
			noSyntaxValidation: false,
		});
		expect(defaults.setCompilerOptions).toHaveBeenCalledWith({
			target: 7,
			allowNonTsExtensions: true,
		});
		expect(defaults.addExtraLib).toHaveBeenCalledWith(
			expect.stringContaining("declare const context:"), "ts:context.d.ts",
		);
		expect(fixture.editor.onDidBlurEditorText).toHaveBeenCalledWith(expect.any(Function));
	});
});
