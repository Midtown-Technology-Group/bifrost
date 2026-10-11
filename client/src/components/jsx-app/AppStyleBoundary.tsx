import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const HOST_CONSTRAINTS: ReadonlyArray<readonly [string, string]> = [
	["display", "block"],
	["position", "relative"],
	["inset", "auto"],
	["z-index", "auto"],
	["width", "100%"],
	["height", "100%"],
	["min-width", "0"],
	["min-height", "0"],
	["margin", "0"],
	["padding", "0"],
	["border", "0"],
	["overflow", "auto"],
	["contain", "strict"],
	["isolation", "isolate"],
];

/**
 * Return the app's Shadow DOM root and pin its host inside the platform layout.
 *
 * App stylesheets are authored by app developers and must not participate in
 * the platform document's cascade. Important inline constraints also prevent a
 * stylesheet's :host rule from turning the shadow host into a fixed overlay.
 */
export function ensureIsolatedAppShadow(host: HTMLElement): ShadowRoot {
	for (const [property, value] of HOST_CONSTRAINTS) {
		host.style.setProperty(property, value, "important");
	}
	return host.shadowRoot ?? host.attachShadow({ mode: "open" });
}

/**
 * Render an inline app through a portal so React context crosses the Shadow DOM
 * boundary while the app stylesheet and DOM remain cascade-isolated.
 */
export function AppStyleBoundary({
	stylesheetHref,
	children,
}: {
	stylesheetHref: string | null;
	children: ReactNode;
}) {
	const hostRef = useRef<HTMLDivElement>(null);
	const [shadowRoot, setShadowRoot] = useState<ShadowRoot | null>(null);

	useEffect(() => {
		const host = hostRef.current;
		if (!host) return;
		setShadowRoot(ensureIsolatedAppShadow(host));
	}, []);

	return (
		<div ref={hostRef} data-bifrost-app-style-boundary="true">
			{shadowRoot
				? createPortal(
						<>
							{stylesheetHref ? (
								<link
									rel="stylesheet"
									href={stylesheetHref}
									data-bifrost-app-stylesheet="true"
								/>
							) : null}
							<div style={{ width: "100%", height: "100%" }}>
								{children}
							</div>
						</>,
						shadowRoot,
					)
				: null}
		</div>
	);
}
