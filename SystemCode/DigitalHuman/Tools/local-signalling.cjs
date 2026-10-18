// Epic UE 5.8 signalling, bound to loopback for a one-person local demonstration.
const http = require("node:http");
const path = require("node:path");
const { SignallingServer, InitLogging } = require(
  "./PixelStreamingInfrastructure/Signalling/dist/cjs/pixelstreamingsignalling.js"
);

InitLogging({ logDir: path.join(__dirname, "../Saved/SignallingLogs"), logLevelConsole: "info", logLevelFile: "warning" });
const server = http.createServer((_request, response) => {
  response.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
  response.end("Local Pixel Streaming signalling is running. Open http://127.0.0.1:8765/agent/ and connect the avatar.\n");
});
const localBrowser = ({ origin }) => !origin || ["http://127.0.0.1:8765", "http://localhost:8765"].includes(origin);
const signalling = new SignallingServer({
  httpServer: server, streamerPort: 8888, peerOptions: { iceServers: [] },
  streamerWsOptions: { host: "127.0.0.1" }, playerWsOptions: { verifyClient: localBrowser },
  maxSubscribers: 1, playerKeepaliveTimeout: 30000,
});
server.listen(8889, "127.0.0.1", () => console.log("Player ws://127.0.0.1:8889; UE ws://127.0.0.1:8888"));
// Holding this reference retains Epic's connection registries until process exit.
process.on("SIGINT", () => { server.close(); void signalling; process.exit(0); });
