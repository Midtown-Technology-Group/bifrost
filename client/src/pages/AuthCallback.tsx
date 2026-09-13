import { AuthTransition } from "@/components/auth/AuthTransition";
import { Card, CardHeader, CardContent } from "@/components/ui/card";
/**
 * Auth Callback Page
 *
 * Handles OAuth callback from identity providers.
 * Exchanges authorization code for tokens and redirects to the app.
 */

import { useEffect, useState, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { useAuth } from "@/contexts/AuthContext";
import { hashOAuthState } from "@/services/auth";
import { AlertCircle } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export function AuthCallback() {
	const navigate = useNavigate();
	const { provider } = useParams<{ provider: string }>();
	const [searchParams] = useSearchParams();
	const { loginWithOAuth } = useAuth();

	const [error, setError] = useState<string | null>(null);
	const hasHandledRef = useRef(false);

	useEffect(() => {
		// Prevent double execution in strict mode
		if (hasHandledRef.current) return;
		hasHandledRef.current = true;

		async function handleCallback() {
			// Get OAuth response parameters
			const code = searchParams.get("code");
			const state = searchParams.get("state");
			const errorParam = searchParams.get("error");
			const errorDescription = searchParams.get("error_description");

			// Check for OAuth error
			if (errorParam) {
				setError(errorDescription || errorParam);
				return;
			}

			// Verify required parameters
			if (!code || !state || !provider) {
				setError(
					"This sign-in link is incomplete. Please start sign-in again.",
				);
				return;
			}

			// Get stored state digest
			// Note: code_verifier is now handled server-side (stored in Redis when init is called)
			const storedState = sessionStorage.getItem("oauth_state");

			try {
				sessionStorage.removeItem("oauth_state");
				sessionStorage.removeItem("oauth_redirect_from");

				if (storedState && (await hashOAuthState(state)) !== storedState) {
					setError("Invalid OAuth state");
					return;
				}

				// Exchange code for tokens (server handles PKCE verification)
				await loginWithOAuth(provider, code, state);

				navigate("/", { replace: true });
			} catch (err) {
				setError(
					err instanceof Error ? err.message : "OAuth login failed",
				);
			}
		}

		handleCallback();
	}, [searchParams, provider, loginWithOAuth, navigate]);

	if (error) {
		return (
			<div className="min-h-svh flex items-center justify-center bg-background px-4 py-8">
				<Card className="w-full max-w-md rounded-[var(--bf-radius-feature)]">
					<CardHeader>
						<h1 className="font-display text-2xl font-semibold tracking-tight">
							Sign-in could not be completed
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
								navigate("/login");
							}}
						>
							Return to Login
						</Button>
					</CardContent>
				</Card>
			</div>
		);
	}

	return <AuthTransition message="Completing sign in…" />;
}

export default AuthCallback;
