import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";
import { $, createComponent } from "./app-code-runtime";

describe("app-code-runtime $ scope", () => {
	describe("global built-ins not shadowed by Lucide icons", () => {
		it("Map resolves to the global Map constructor", () => {
			expect($.Map).toBe(globalThis.Map);
			const Ctor = $.Map as MapConstructor;
			const m = new Ctor<string, number>();
			m.set("a", 1);
			expect(m.get("a")).toBe(1);
		});

		it("Set resolves to the global Set constructor", () => {
			expect($.Set).toBe(globalThis.Set);
			const Ctor = $.Set as SetConstructor;
			const s = new Ctor<string>();
			s.add("a");
			expect(s.has("a")).toBe(true);
		});

		it("WeakMap resolves to the global WeakMap constructor", () => {
			expect($.WeakMap).toBe(globalThis.WeakMap);
		});

		it("WeakSet resolves to the global WeakSet constructor", () => {
			expect($.WeakSet).toBe(globalThis.WeakSet);
		});

		it("Date resolves to the global Date constructor", () => {
			expect($.Date).toBe(globalThis.Date);
			const Ctor = $.Date as DateConstructor;
			const d = new Ctor(0);
			expect(d.getTime()).toBe(0);
		});
	});

	it("does not let direct scope entries shadow browser globals", () => {
		const Component = createComponent(
			`__defaultExport__ = function GlobalProof() {
        const file = new File(["proof"], "proof.txt");
        const values = new Map([["name", file.name]]);
        return React.createElement("span", null, values.get("name"));
      };`,
			{
				File: (() => null) as never,
				Map: (() => null) as never,
			},
			true,
		);

		render(React.createElement(Component));
		expect(screen.getByText("proof.txt")).toBeInTheDocument();
	});
});
