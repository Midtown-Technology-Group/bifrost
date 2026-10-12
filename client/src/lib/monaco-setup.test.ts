import { beforeEach, describe, expect, it, vi } from "vitest";

const workers = vi.hoisted(() => ({
	editor: vi.fn(function EditorWorker() {}),
	json: vi.fn(function JsonWorker() {}),
	css: vi.fn(function CssWorker() {}),
	html: vi.fn(function HtmlWorker() {}),
	typescript: vi.fn(function TypeScriptWorker() {}),
}));
const config = vi.hoisted(() => vi.fn());

vi.mock("@monaco-editor/react", () => ({ loader: { config } }));
vi.mock("monaco-editor", () => ({ editor: { getModels: vi.fn() } }));
vi.mock("monaco-editor/editor/editor.worker.js?worker", () => ({ default: workers.editor }));
vi.mock("monaco-editor/language/json/json.worker.js?worker", () => ({ default: workers.json }));
vi.mock("monaco-editor/language/css/css.worker.js?worker", () => ({ default: workers.css }));
vi.mock("monaco-editor/language/html/html.worker.js?worker", () => ({ default: workers.html }));
vi.mock("monaco-editor/language/typescript/ts.worker.js?worker", () => ({ default: workers.typescript }));
vi.mock("@/stores/workflowsStore", () => ({ useWorkflowsStore: {} }));

describe("configureMonaco", () => {
	beforeEach(() => {
		vi.resetModules();
		vi.clearAllMocks();
	});

	it("gives the loader and model inspectors the same installed module, once", async () => {
		const { configureMonaco } = await import("./monaco-setup");
		const monaco = await import("monaco-editor");
		configureMonaco();
		const environment = self.MonacoEnvironment;
		configureMonaco();
		expect(config).toHaveBeenCalledExactlyOnceWith({ monaco });
		expect(Reflect.get(window, "monaco")).toBe(monaco);
		expect(self.MonacoEnvironment).toBe(environment);
	});

	it.each([
		["json", "json"],
		["css", "css"], ["scss", "css"], ["less", "css"],
		["html", "html"], ["handlebars", "html"], ["razor", "html"],
		["javascript", "typescript"], ["typescript", "typescript"],
		["python", "editor"], ["yaml", "editor"], ["editorWorkerService", "editor"],
	] as const)("creates a local worker for %s", async (label, kind) => {
		const { configureMonaco } = await import("./monaco-setup");
		configureMonaco();
		const first = self.MonacoEnvironment!.getWorker!("", label);
		const second = self.MonacoEnvironment!.getWorker!("", label);
		expect(first).toBeInstanceOf(workers[kind]);
		expect(second).toBeInstanceOf(workers[kind]);
		expect(second).not.toBe(first);
	});
});


describe("initializeMonaco", () => {
	it("configures the public TypeScript namespace without the removed language alias", async () => {
		vi.resetModules();
		const defaults = () => ({
			setCompilerOptions: vi.fn(),
			setDiagnosticsOptions: vi.fn(),
		});
		const monaco = {
			languages: {
				getLanguages: () => [{ id: "python" }],
				setLanguageConfiguration: vi.fn(),
				registerCodeLensProvider: vi.fn(),
			},
			editor: { registerCommand: vi.fn() },
			typescript: {
				typescriptDefaults: defaults(),
				javascriptDefaults: defaults(),
				ScriptTarget: { ESNext: 99 },
				ModuleResolutionKind: { NodeJs: 2 },
				ModuleKind: { ESNext: 99 },
				JsxEmit: { React: 2 },
			},
		};
		const { initializeMonaco } = await import("./monaco-setup");
		await initializeMonaco(monaco as unknown as typeof import("monaco-editor"));
		for (const language of ["typescriptDefaults", "javascriptDefaults"] as const) {
			expect(monaco.typescript[language].setCompilerOptions).toHaveBeenCalledWith(
				expect.objectContaining({ jsx: 2, allowJs: true, noEmit: true }),
			);
			expect(monaco.typescript[language].setDiagnosticsOptions).toHaveBeenCalledWith({
				noSemanticValidation: true,
				noSyntaxValidation: false,
			});
		}
		expect(monaco.languages.registerCodeLensProvider).toHaveBeenCalledWith(
			"python", expect.objectContaining({ provideCodeLenses: expect.any(Function) }),
		);
	});
});
