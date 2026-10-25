import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
	browserRunExitCode,
	buildReport,
	markdown,
	validateLedger,
} from "./acceptance-report.mjs";

const binding = {
	spec: "form.admin.spec.ts",
	title: "[FORM-01] submit",
	project: "platform-admin",
};
const ledger = {
	inventoryComplete: false,
	journeys: [
		{ id: "FORM-01", area: "Forms", title: "Submit", tests: [binding] },
		{ id: "FORM-02", area: "Forms", title: "Edit", tests: [] },
	],
};
function result(test = {}) {
	return {
		config: {
			metadata: { sourceRevision: "candidate", sourceDirty: "true" },
		},
		suites: [
			{
				suites: [
					{
						specs: [
							{
								file: binding.spec,
								title: binding.title,
								tests: [
									{
										projectName: binding.project,
										expectedStatus: "passed",
										status: "expected",
										results: [{ status: "passed" }],
										...test,
									},
								],
							},
						],
					},
				],
			},
		],
	};
}

test("joins nested results by exact file/title/project and preserves missing work", () => {
	const report = buildReport(ledger, result());
	assert.deepEqual(
		report.rows.map((row) => row.status),
		["passed", "missing automation"],
	);
	assert.equal(report.source.sourceRevision, "candidate");
	assert.equal(report.inventoryComplete, false);
	assert.equal(
		buildReport(ledger, result({ projectName: "org-user" })).rows[0].status,
		"not run",
	);
});

test("skips, retries, expected failures and interruptions never become passing evidence", () => {
	for (const override of [
		{ results: [{ status: "skipped" }] },
		{ results: [{ status: "failed" }, { status: "passed" }] },
		{ expectedStatus: "failed", results: [{ status: "failed" }] },
		{ results: [] },
		{ status: "unexpected" },
	])
		assert.equal(
			buildReport(ledger, result(override)).rows[0].status,
			"failed or incomplete",
		);
});

test("a desktop pass cannot hide an unrun mobile case or run-level error", () => {
	const both = structuredClone(ledger);
	both.journeys[0].tests.push({
		...binding,
		title: "[FORM-01] mobile submit",
	});
	const data = result();
	data.errors = [{ message: "setup failure" }];
	const report = buildReport(both, data);
	assert.equal(report.rows[0].status, "partially run");
	assert.equal(report.runErrors, 1);
	assert.equal(buildReport(ledger, null).rows[0].status, "not run");
});

test("invalid ledger references fail loudly", () => {
	assert.doesNotThrow(() => validateLedger(ledger, () => binding.title));
	assert.throws(
		() => validateLedger(ledger, () => "renamed test"),
		/Missing test title/,
	);
	assert.throws(
		() =>
			validateLedger(
				{ journeys: [...ledger.journeys, ledger.journeys[0]] },
				() => binding.title,
			),
		/Duplicate/,
	);
});

test("whole-run failures remain visible even when all bound cases pass", () => {
	const data = result();
	data.stats = { expected: 1, unexpected: 1, skipped: 2, flaky: 0 };
	const report = buildReport(ledger, data);
	assert.equal(report.rows[0].status, "passed");
	assert.match(
		markdown(report),
		/1 expected, 1 unexpected, 2 skipped, 0 flaky/,
	);
});

test("a fail-once-then-pass outside the ledger still fails the browser run", () => {
	const outside = {
		stats: { expected: 0, unexpected: 0, skipped: 0, flaky: 1 },
		suites: [
			{
				specs: [
					{
						file: "not-in-ledger.spec.ts",
						title: "fails once then passes",
						tests: [
							{
								projectName: "chromium",
								expectedStatus: "passed",
								status: "flaky",
								results: [
									{ status: "failed" },
									{ status: "passed" },
								],
							},
						],
					},
				],
			},
		],
	};
	assert.equal(browserRunExitCode(0, outside), 1);
	assert.deepEqual(
		buildReport(ledger, outside).rows.map((row) => row.status),
		["not run", "missing automation"],
	);
	const clean = structuredClone(outside);
	clean.stats.flaky = 0;
	clean.suites[0].specs[0].title = "passes once";
	clean.suites[0].specs[0].tests[0].status = "expected";
	clean.suites[0].specs[0].tests[0].results = [{ status: "passed" }];
	assert.equal(browserRunExitCode(0, clean), 0);
	assert.equal(browserRunExitCode(1, outside), 1);
	assert.equal(browserRunExitCode(2, clean), 2);
	assert.equal(browserRunExitCode(null, clean), 1);
	assert.equal(
		browserRunExitCode(0, { stats: { flaky: 1 }, suites: [] }),
		1,
	);
});

test("an earlier failed, timed-out, or interrupted attempt still fails a zero Playwright exit", () => {
	for (const first of ["failed", "timedOut", "interrupted"]) {
		assert.equal(
			browserRunExitCode(
				0,
				result({
					status: "expected",
					results: [{ status: first }, { status: "passed" }],
				}),
			),
			1,
			first,
		);
	}
	assert.equal(
		browserRunExitCode(
			0,
			result({ status: "flaky", results: [{ status: "passed" }] }),
		),
		1,
	);
	assert.equal(
		browserRunExitCode(
			1,
			result({
				status: "unexpected",
				results: [{ status: "passed" }, { status: "failed" }],
			}),
		),
		1,
	);
});

test("the browser wrapper applies the retried-pass failure to the process exit", () => {
	const wrapper = readFileSync(
		new URL("./run-playwright.mjs", import.meta.url),
		"utf8",
	);
	assert.match(wrapper, /const recovered = retriedPasses\(results\)/);
	assert.match(
		wrapper,
		/const browserExit = browserRunExitCode\(exitCode, results\)/,
	);
	assert.match(wrapper, /process\.exitCode = browserExit/);
});

test("rendered evidence distinguishes controlled setup from real platform journeys", () => {
	const mixed = {
		...ledger,
		journeys: [
			{
				...ledger.journeys[0],
				tests: [{ ...binding, proof: "mixed-setup-real-mutation" }],
			},
		],
	};
	const output = markdown(buildReport(mixed, result()));
	assert.match(output, /Evidence type/);
	assert.match(output, /mixed-setup-real-mutation/);
});
