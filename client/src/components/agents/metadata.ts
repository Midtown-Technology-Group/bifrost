/** Preserve strings and display structured API metadata as JSON text. */
export function formatMetadataValue(value: unknown): string {
	return typeof value === "string" ? value : (JSON.stringify(value) ?? "");
}
