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
