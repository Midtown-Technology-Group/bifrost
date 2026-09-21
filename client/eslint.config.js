import { plugin as shadcn } from "@shadcn/lint";
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

const disabledReactCompilerRules = [
	"config",
	"error-boundaries",
	"gating",
	"globals",
	"immutability",
	"incompatible-library",
	"preserve-manual-memoization",
	"purity",
	"refs",
	"set-state-in-effect",
	"set-state-in-render",
	"static-components",
	"unsupported-syntax",
	"use-memo",
];

export default tseslint.config(
	{ ignores: ["dist"] },
	{
		extends: [js.configs.recommended, ...tseslint.configs.recommended],
		files: ["**/*.{ts,tsx}"],
		languageOptions: {
			ecmaVersion: 2020,
			globals: globals.browser,
		},
		plugins: {
			"react-hooks": reactHooks,
			"react-refresh": reactRefresh,
			shadcn,
		},
		rules: {
			...reactHooks.configs.recommended.rules,
			// eslint-plugin-react-hooks v7 enables React Compiler diagnostics in
			// its recommended set. This codebase is not compiler-clean yet, so keep
			// the traditional hooks checks without turning existing compiler
			// diagnostics into merge blockers.
			...Object.fromEntries(
				disabledReactCompilerRules.map((ruleName) => [
					`react-hooks/${ruleName}`,
					"off",
				]),
			),
			"react-refresh/only-export-components": "off",
			// Trial: @shadcn/lint design-system drift check (warn-only, non-blocking).
			// NOTE: scanAllStrings was trialled and reverted — it flags non-class
			// strings (SVG attributes like stroke-width, status slugs like
			// "to-review"). Re-enable per-directory if data-driven class maps
			// (e.g. SolutionCounts) need coverage.
			"shadcn/no-raw-colors": "warn",
			// Surveyed and parked: no-arbitrary-values (157 distinct classes;
			// 104 are sanctioned var(--bf-*) refs, rest are magic numbers like
			// text-[11px] plus structural one-offs like safe-area insets).
			// Do NOT enable with a rounded-*/group allowlist: patterns can't
			// express "var() only" (verified: rounded-[var(*)] doesn't match,
			// rounded-* would also blind rounded-[2px]). The way out is
			// migrating var(--bf-*) usages to the (--) shorthand syntax the
			// rule passes natively, then enabling it for the remainder.
			// no-inline-styles parked (215 hits, many dynamic brand values —
			// needs triage plus a CSS-var migration pattern first).
			// no-unknown-classes kept: "prose"/"prose-*" covers the
			// @tailwindcss/typography base classes; prose-p:/prose-headings:
			// element variants are a known rule blind spot (valid plugin
			// syntax it doesn't recognize).
			"shadcn/no-unknown-classes": ["warn", { allow: ["prose", "prose-*"] }],
			// require-static-classes kept (27 hits): dynamic className
			// construction blinds every other rule, so these are coverage
			// holes worth closing one by one.
			"shadcn/require-static-classes": "warn",
			// no-restyle: Button keeps strict layout-only ownership so sizing
			// goes through size= and tone through variant=. Surface/slot
			// components and content primitives leave spacing+typography to
			// callers (descriptive contracts derived from current usage);
			// color/shape/effects stay owned. Badge keeps color teeth so
			// semantic hand-rolls become variants instead of overrides.
			"shadcn/no-restyle": [
				"warn",
				{
					allow: ["layout"],
					contracts: [
						{
							pattern:
								"^(Card|CardContent|CardHeader|CardFooter|CardTitle|CardDescription)$",
							allow: ["layout", "spacing", "typography"],
						},
						{
							pattern:
								"^(DialogContent|DialogHeader|DialogFooter|DialogTitle|DialogDescription|SheetContent|SheetHeader|SheetFooter|SheetTitle|SheetDescription|AlertDialogContent|AlertDialogHeader|AlertDialogFooter|AlertDialogTitle|AlertDialogDescription|PopoverContent|AlertTitle|AlertDescription)$",
							allow: ["layout", "spacing", "typography"],
						},
						{
							pattern:
								"^(Input|Textarea|Label|DataTableCell|DataTableHead|CollapsibleTrigger|Badge)$",
							allow: ["layout", "spacing", "typography"],
						},
					],
				},
			],
			"no-console": ["warn", { allow: ["warn", "error"] }], // Allow console.warn and console.error, but warn about console.log
			// Allow underscore-prefixed unused variables
			"@typescript-eslint/no-unused-vars": [
				"error",
				{
					argsIgnorePattern: "^_",
					varsIgnorePattern: "^_",
				},
			],
		},
	},
	// Design-system implementation files style themselves; no-restyle
	// governs pages passing className into components, not components
	// composing primitives (per @shadcn/lint setup docs).
	{
		files: ["**/components/ui/**"],
		rules: {
			"shadcn/no-restyle": "off",
		},
	},
);
