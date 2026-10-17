import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { AlertCircle, Loader2, ShieldCheck, Terminal } from "lucide-react";

import { Logo } from "@/components/branding/Logo";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardFooter,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { useAuth } from "@/contexts/AuthContext";
import { ACCESS_TOKEN_KEY } from "@/lib/auth-token";
import { getCsrfToken } from "@/lib/api-client";
import { useApplicationName } from "@/lib/applicationName";

interface PendingCliAuthorization {
	transaction_id: string;
	client_name: string;
	redirect_uri: string;
	expires_in: number;
}

interface CliAuthorizationResult {
	redirect_url: string;
}

interface CliAuthorizePageProps {
	redirectTo?: (url: string) => void;
}

function authHeaders(includeJson = false): Record<string, string> {
	const headers: Record<string, string> = {};
	const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY);
	if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
	if (includeJson) headers["Content-Type"] = "application/json";
	const csrfToken = getCsrfToken();
	if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
	return headers;
}

async function responseDetail(response: Response, fallback: string) {
	const body = await response.json().catch(() => null);
	return typeof body?.detail === "string" ? body.detail : fallback;
}

function isSafeCliRedirect(value: string): boolean {
	try {
		const target = new URL(value);
		return (
			target.protocol === "http:" &&
			["127.0.0.1", "localhost"].includes(target.hostname) &&
			Boolean(target.port)
		);
	} catch {
		return false;
	}
}

export function CliAuthorizePage({
	redirectTo = (url) => window.location.assign(url),
}: CliAuthorizePageProps) {
	const applicationName = useApplicationName();
	const location = useLocation();
	const navigate = useNavigate();
	const { isAuthenticated, isLoading: authLoading, user } = useAuth();
	const transactionId = useMemo(
		() => new URLSearchParams(location.search).get("transaction_id"),
		[location.search],
	);
	const returnTo = `${location.pathname}${location.search}`;

	const [pending, setPending] = useState<PendingCliAuthorization | null>(
		null,
	);
	const [loading, setLoading] = useState(true);
	const [submitting, setSubmitting] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (!authLoading && !isAuthenticated) {
			navigate(`/login?returnTo=${encodeURIComponent(returnTo)}`, {
				replace: true,
			});
		}
	}, [authLoading, isAuthenticated, navigate, returnTo]);

	useEffect(() => {
		if (authLoading || !isAuthenticated) return;
		if (!transactionId) {
			setError(
				"This CLI authorization link is missing its transaction ID.",
			);
			setLoading(false);
			return;
		}

		const controller = new AbortController();
		const loadPendingRequest = async () => {
			try {
				const response = await fetch(
					`/auth/cli/authorize?${new URLSearchParams({ transaction_id: transactionId })}`,
					{
						headers: authHeaders(),
						credentials: "same-origin",
						signal: controller.signal,
					},
				);
				if (response.status === 401) {
					navigate(
						`/login?returnTo=${encodeURIComponent(returnTo)}`,
						{
							replace: true,
						},
					);
					return;
				}
				if (!response.ok) {
					throw new Error(
						await responseDetail(
							response,
							"Unable to load this CLI authorization request.",
						),
					);
				}
				setPending((await response.json()) as PendingCliAuthorization);
			} catch (cause) {
				if (controller.signal.aborted) return;
				setError(
					cause instanceof Error
						? cause.message
						: "Unable to load this CLI authorization request.",
				);
			} finally {
				if (!controller.signal.aborted) setLoading(false);
			}
		};

		void loadPendingRequest();
		return () => controller.abort();
	}, [authLoading, isAuthenticated, navigate, returnTo, transactionId]);

	const confirmAuthorization = async () => {
		if (!pending || submitting) return;
		setSubmitting(true);
		setError(null);
		try {
			const response = await fetch("/auth/cli/authorize", {
				method: "POST",
				headers: authHeaders(true),
				credentials: "same-origin",
				body: JSON.stringify({
					transaction_id: pending.transaction_id,
				}),
			});
			if (!response.ok) {
				throw new Error(
					await responseDetail(
						response,
						"Unable to authorize this CLI request.",
					),
				);
			}
			const result = (await response.json()) as CliAuthorizationResult;
			if (!isSafeCliRedirect(result.redirect_url)) {
				throw new Error(
					"The CLI callback address is not a safe localhost URL.",
				);
			}
			redirectTo(result.redirect_url);
		} catch (cause) {
			setError(
				cause instanceof Error
					? cause.message
					: "Unable to authorize this CLI request.",
			);
			setSubmitting(false);
		}
	};

	if (authLoading || loading) {
		return (
			<div className="min-h-svh flex items-center justify-center bg-background">
				<Loader2 className="size-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
				<span className="sr-only">
					Loading CLI authorization request
				</span>
			</div>
		);
	}

	return (
		<div className="min-h-svh flex items-center justify-center bg-background px-4 py-8">
			<Card className="w-full max-w-lg rounded-[var(--bf-radius-feature)]">
				<CardHeader className="space-y-4 text-center">
					<div className="flex justify-center">
						<Logo
							type="square"
							className="size-14"
							alt={applicationName}
						/>
					</div>
					<div className="mx-auto flex size-10 items-center justify-center rounded-full bg-muted">
						<Terminal className="size-5" aria-hidden="true" />
					</div>
					<CardTitle>
						<h1 className="text-xl">Authorize Bifrost CLI</h1>
					</CardTitle>
					<CardDescription>
						Only approve this request if you started a CLI login on
						this device.
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					{error && (
						<Alert variant="destructive">
							<AlertCircle aria-hidden="true" />
							<AlertTitle>Authorization unavailable</AlertTitle>
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}

					{pending && (
						<>
							<Alert>
								<ShieldCheck aria-hidden="true" />
								<AlertTitle>
									{pending.client_name} is requesting access
								</AlertTitle>
								<AlertDescription>
									This will create CLI access and refresh
									tokens for {user?.email}.
								</AlertDescription>
							</Alert>
							<div className="space-y-2">
								<p className="text-sm font-medium">
									Callback address
								</p>
								<code className="block break-all rounded-[var(--bf-radius-surface)] border bg-muted px-3 py-2 text-xs">
									{pending.redirect_uri}
								</code>
								<p className="text-xs text-muted-foreground">
									The request expires in at most{" "}
									{pending.expires_in} seconds.
								</p>
							</div>
						</>
					)}
				</CardContent>
				<CardFooter className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
					<Button
						variant="outline"
						onClick={() => navigate("/")}
						disabled={submitting}
					>
						Cancel
					</Button>
					<Button
						onClick={() => void confirmAuthorization()}
						disabled={!pending || submitting}
					>
						{submitting && (
							<Loader2 className="animate-spin motion-reduce:animate-none" />
						)}
						Approve CLI access
					</Button>
				</CardFooter>
			</Card>
		</div>
	);
}
