import { AuthTransition } from "@/components/auth/AuthTransition";
import { Card, CardHeader, CardContent } from "@/components/ui/card";
/**
 * MCP OAuth Callback Page
 *
 * Handles the OAuth callback for MCP (Model Context Protocol) clients.
 * This page is loaded after the user authenticates via Bifrost login.
 *
 * The flow:
 * 1. MCP client (e.g., Claude Desktop) initiates OAuth at /authorize
 * 2. User is redirected to Bifrost login
 * 3. After login, browser navigates to /mcp/callback with internal_state
 * 4. This component fetches /mcp/callback via XHR (works with Vite proxy)
 * 5. Server returns redirect_url plus a presentation-only MCP client label
 * 6. Component opens the URL and tells the user which client to return to
 */

import { useEffect, useState, useRef } from "react";
import { useSearchParams } from "react-router";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

type McpClientInfo = {
	label: string;
};

export function MCPCallback() {
	const [searchParams] = useSearchParams();
	const [error, setError] = useState<string | null>(null);
	const [success, setSuccess] = useState(false);
	const [mcpClient, setMcpClient] = useState<McpClientInfo | null>(null);
	const [redirectUrl, setRedirectUrl] = useState<string | null>(null);
	const hasHandledRef = useRef(false);

	useEffect(() => {
		// Prevent double execution in strict mode
		if (hasHandledRef.current) return;
		hasHandledRef.current = true;

		async function handleCallback() {
			const internalState = searchParams.get("internal_state");

			if (!internalState) {
				setError(
					"This authorization link is incomplete. Start the connection again from your app.",
				);
				return;
			}

			try {
				// Fetch the callback endpoint via XHR - Vite proxy handles this
				// The server returns a JSON response with redirect_url when Accept: application/json
				const response = await fetch(
					`/mcp/callback?internal_state=${internalState}`,
					{
						headers: {
							Accept: "application/json",
						},
						credentials: "include", // Include cookies for auth
					},
				);

				if (!response.ok) {
					const data = await response.json().catch(() => ({}));
					setError(
						data.error_description ||
							data.error ||
							`Callback failed: ${response.status}`,
					);
					return;
				}

				const data = await response.json();

				if (
					data.mcp_client &&
					typeof data.mcp_client === "object" &&
					typeof data.mcp_client.label === "string"
				) {
					setMcpClient({ label: data.mcp_client.label });
				}

				if (data.redirect_url) {
					// Show success state first
					setSuccess(true);
					setRedirectUrl(data.redirect_url);
				} else {
					setError("No redirect URL returned from server");
				}
			} catch (err) {
				setError(
					err instanceof Error ? err.message : "MCP callback failed",
				);
			}
		}

		handleCallback();
	}, [searchParams]);

	useEffect(() => {
		if (!redirectUrl) return;
		// Custom-scheme callbacks (cursor://, claude://) hand off to the host app.
		// HTTPS callbacks (claude.ai, cursor.com) still navigate so the host can finish.
		const redirectTimer = setTimeout(() => {
			window.location.replace(redirectUrl);
		}, 100);
		return () => {
			clearTimeout(redirectTimer);
		};
	}, [redirectUrl]);

	if (error) {
		return (
			<div className="min-h-svh flex items-center justify-center bg-background px-4 py-8">
				<Card className="w-full max-w-md rounded-[var(--bf-radius-feature)]">
					<CardHeader>
						<h1 className="font-display text-2xl font-semibold tracking-tight">
							Authorization could not be completed
						</h1>
					</CardHeader>
					<CardContent className="space-y-4">
						<Alert variant="destructive">
							<AlertCircle className="size-4" />
							<AlertDescription className="[overflow-wrap:anywhere]">
								{error}
							</AlertDescription>
						</Alert>
						<Button
							className="min-h-11 w-full"
							onClick={() => {
								window.location.href = "/";
							}}
						>
							Return to Home
						</Button>
					</CardContent>
				</Card>
			</div>
		);
	}

	if (success) {
		return (
			<div className="min-h-svh flex items-center justify-center bg-background px-4 py-8">
				<div className="w-full max-w-md space-y-4 rounded-[var(--bf-radius-feature)] border border-border bg-card p-6 text-center">
					<CheckCircle2 className="h-12 w-12 mx-auto text-[var(--bf-success)]" />
					<h1 className="font-display text-2xl font-semibold">
						Authorization Complete
					</h1>
					<p className="text-muted-foreground">
						You can close this tab and return to{" "}
						{mcpClient?.label ?? "your MCP client"}.
					</p>
				</div>
			</div>
		);
	}

	return <AuthTransition message="Completing MCP authorization…" />;
}

export default MCPCallback;
