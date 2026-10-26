// Each HTTP connection owns its framing and keep-alive policy. In particular,
// forwarding Connection prevents Node from advertising its own idle timeout.
export function connectionHeaders(headers) {
 const excluded = new Set(["connection", "keep-alive", "proxy-connection",
  "proxy-authenticate", "proxy-authorization", "te", "trailer",
  "transfer-encoding", "upgrade"]);
 const connection = headers.connection;
 for (const name of (Array.isArray(connection) ? connection.join(",") : connection ?? "").split(",")) {
  excluded.add(name.trim().toLowerCase());
 }
 return Object.fromEntries(Object.entries(headers).filter(([name]) =>
  !excluded.has(name.toLowerCase())));
}

// APIRequestContext can retain a socket across browser navigation. Explicitly
// close completed HTTP responses rather than racing the local server idle close.
// WebSocket upgrades use their own tunnel and do not pass through this function.
export function responseConnectionHeaders(headers) {
 return {...connectionHeaders(headers), connection: "close"};
}
