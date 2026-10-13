/**
 * Component tests for InputMappingForm.
 *
 * The form renders one text input per workflow parameter regardless of the
 * parameter type (so users can enter `{{ template }}` strings). Covers:
 *   - a field is rendered for each parameter with its label + type hint
 *   - editing a field propagates the string through onChange
 *   - the helper panel is present so users know template syntax
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen, fireEvent } from "@/test-utils";
import { InputMappingForm, normalizeInputMapping } from "./InputMappingForm";

function renderForm(
	overrides: Partial<Parameters<typeof InputMappingForm>[0]> = {},
) {
	const onChange = vi.fn();
	const utils = renderWithProviders(
		<InputMappingForm
			parameters={[
				{
					name: "ticket_id",
					type: "int",
					label: "Ticket",
					required: false,
				},
				{ name: "note", type: "str", required: false },
			]}
			values={{}}
			onChange={onChange}
			{...overrides}
		/>,
	);
	return { ...utils, onChange };
}

describe("InputMappingForm", () => {
	it("renders one input per parameter with the label or name", () => {
		renderForm();
		expect(screen.getByLabelText(/ticket/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/note/i)).toBeInTheDocument();
	});

	it("propagates edits through onChange with the raw string", () => {
		const { onChange } = renderForm();

		fireEvent.change(screen.getByLabelText(/ticket/i), {
			target: { value: "{{ payload.id }}" },
		});

		expect(onChange).toHaveBeenLastCalledWith({
			ticket_id: "{{ payload.id }}",
		});
	});

	it("renders undefined for cleared fields so the mapping stays sparse", () => {
		const { onChange } = renderForm({ values: { note: "hi" } });

		fireEvent.change(screen.getByLabelText(/note/i), {
			target: { value: "" },
		});

		expect(onChange).toHaveBeenLastCalledWith({ note: undefined });
	});

	it("includes the template-variable helper panel", () => {
		renderForm();
		expect(screen.getByText(/template variables/i)).toBeInTheDocument();
		expect(screen.getByText(/auto-injected context/i)).toBeInTheDocument();
	});

	it("renders existing structured values as JSON", () => {
		renderForm({
			parameters: [{ name: "payload", type: "dict", required: false }],
			values: { payload: { enabled: true } },
		});
		expect(screen.getByLabelText(/payload/i)).toHaveValue(
			'{"enabled":true}',
		);
	});
});

describe("normalizeInputMapping", () => {
	const parameters = [
		{ name: "payload", type: "dict", required: false },
		{ name: "items", type: "list", required: false },
		{ name: "raw", type: "json", required: false },
	];

	it("parses structured static values and preserves templates", () => {
		expect(
			normalizeInputMapping(parameters, {
				payload: '{"enabled":true}',
				items: "[1,2]",
				raw: "{{ payload.body }}",
			}),
		).toEqual({
			mapping: {
				payload: { enabled: true },
				items: [1, 2],
				raw: "{{ payload.body }}",
			},
			errors: [],
		});
	});

	it("rejects invalid structured static values", () => {
		const result = normalizeInputMapping(parameters, {
			payload: "[]",
			items: "{}",
		});
		expect(result.mapping).toBeUndefined();
		expect(result.errors).toHaveLength(2);
	});
});
