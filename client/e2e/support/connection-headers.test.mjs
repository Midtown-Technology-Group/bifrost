import assert from "node:assert/strict";
import http from "node:http";
import { once } from "node:events";
import test from "node:test";
import { connectionHeaders } from "./connection-headers.mjs";

test("proxy removes hop headers and connection-nominated fields, retaining cookies and content", () => {
 assert.deepEqual(connectionHeaders({connection: "keep-alive, X-Private-Hop",
  "keep-alive": "timeout=75", "x-private-hop": "internal",
  "transfer-encoding": "chunked", cookie: "test=value",
  "content-type": "application/json", "content-length": "2"}),
  {cookie: "test=value", "content-type": "application/json", "content-length": "2"});
});

test("proxied response advertises the local server idle policy, not upstream policy", async () => {
 const server = http.createServer((_request, response) => {
  response.writeHead(200, connectionHeaders({connection: "keep-alive",
   "keep-alive": "timeout=75", "content-type": "application/json"}));
  response.end("{}");
 });
 server.listen(0, "127.0.0.1");
 await once(server, "listening");
 const agent = new http.Agent({keepAlive: true});
 try {
  const headers = await new Promise((resolve, reject) => {
   http.get({host: "127.0.0.1", port: server.address().port, agent}, response => {
    response.resume();
    response.once("end", () => resolve(response.headers));
   }).once("error", reject);
  });
  assert.equal(headers["keep-alive"], `timeout=${server.keepAliveTimeout / 1000}`);
  assert.equal(headers["content-type"], "application/json");
 } finally {
  agent.destroy();
  await new Promise(resolve => server.close(resolve));
 }
});
