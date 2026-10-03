"""Connection-scoped MCP tool adapter and server-signed speech completion receipts.

Responsibilities: Expose finish_current_answer over the existing authenticated Agent WebSocket.
Implementation: Map MCP JSON-RPC initialization, discovery, and call envelopes to the existing
Answer command. Only final STT receipts matching owner, question, and full text permit automatic
calls.
Related Modules: speech.socket issues receipts; agent_socket retains safety, persistence, and
deduplication.

Declaration Index:
- transcript_digest: Hash exactly the stripped final transcript for receipt binding.
- issue_completion_receipt: Mint a short-lived server-signed completion receipt.
- verify_completion_receipt: Reject changed text, owner/question mismatch and expired receipts.
- tool_result: Wrap approved business payloads as MCP content and structuredContent.
- InterviewMCP: One connection's MCP handshake, discovery and fixed tool dispatch.
- InterviewMCP.__init__: Bind the authenticated owner and initialize handshake state.
- InterviewMCP.handle: Validate JSON-RPC and return either a control reply or normalized Answer
  data.

Variable Index:
- RECEIPT_SALT: Domain separation for Django timestamp signing of speech decisions.
- FINISH_TOOL: Fixed discoverable tool schema; the LLM cannot choose arbitrary backend operations.

Constraints:
Custom WebSocket transport, not Streamable HTTP; tool call IDs must be UUIDs to
reuse database deduplication. No global live-session registry or cross-worker memory
dependency.
Receipts expire after 60s; current-question and request checks still occur in agent_socket.
"""

import hashlib
import json
from uuid import UUID

from django.core import signing

RECEIPT_SALT = "interview.answer-completion.v1"
FINISH_TOOL = {
    "name": "finish_current_answer",
    "description": "Finish a confirmed voice answer; evaluate it and run the next interview step.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "question_id": {"type": "string", "minLength": 1},
            "answer_text": {"type": "string", "minLength": 1},
            "completion_receipt": {"type": "string", "minLength": 1},
            "progress_events": {"type": "boolean", "default": False},
        },
        "required": ["question_id", "answer_text", "completion_receipt"],
        "additionalProperties": False,
    },
}


def transcript_digest(text):
    """Inputs: final text. Outputs: SHA-256 digest of stripped UTF-8, with no text logging.

    """
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def issue_completion_receipt(owner_id, question_id, text):
    """Inputs: authenticated STT owner, requested question and finalized nonempty text.
    Outputs: timestamp-signed receipt. Logic: bind all three values; no persistent write.
    Constraints: caller must have confirmed intent, silence and unchanged final transcript.
    """
    return signing.dumps(
        {"owner": owner_id, "question": question_id, "text": transcript_digest(text)},
        salt=RECEIPT_SALT,
    )


def verify_completion_receipt(receipt, owner_id, question_id, text):
    """Inputs: client receipt and trusted owner plus claimed question/text. Outputs: None.
    Logic: authenticate signature/60s expiry and exact bindings. Constraints: malformed,
    expired or changed receipts raise ValueError without echoing transcript or token.
    """
    try:
        payload = signing.loads(receipt, salt=RECEIPT_SALT, max_age=60)
    except signing.BadSignature as exc:
        raise ValueError("Invalid or expired answer completion receipt") from exc
    if payload != {"owner": owner_id, "question": question_id, "text": transcript_digest(text)}:
        raise ValueError("Answer completion receipt does not match this answer")


def tool_result(request_id, payload):
    """Inputs: UUID and already approved business payload. Outputs: MCP result envelope.
    Logic: provide text and structuredContent from the same payload; never bypass safety
    review.
    """
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload,
            "isError": False,
        },
    }


class InterviewMCP:
    """Functionality: connection-local MCP server. Logic: initialize -> notification -> tools.
    Constraints: fixed tool only, no execution or model access inside this protocol adapter.
    """

    def __init__(self, owner_id):
        """Inputs: upstream authenticated owner. Outputs: uninitialized isolated protocol
        state.
        """
        self.owner_id, self.negotiated, self.ready = owner_id, False, False

    def handle(self, data):
        """Inputs: decoded JSON-RPC object. Outputs: (reply, normalized command), either may be
        None.
        Logic: enforce lifecycle; validate fixed schema and receipt before mapping tool calls
        to
        Answer. Constraints: IDs on tool calls are UUIDs; errors propagate as safe ValueError.
        Unknown methods/fields are rejected; initialized notification produces no reply.
        """
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0":
            raise ValueError("Expected JSON-RPC 2.0")
        if set(data) - {"jsonrpc", "id", "method", "params"}:
            raise ValueError("Unknown MCP envelope fields")
        method, identifier, params = data.get("method"), data.get("id"), data.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("Expected MCP parameters object")
        if method == "notifications/initialized":
            if not self.negotiated or "id" in data or params:
                raise ValueError("Initialize MCP before sending initialized notification")
            self.ready = True
            return None, None
        if type(identifier) not in (str, int):
            raise ValueError("Expected MCP request ID")
        if method == "initialize":
            if self.negotiated or set(params) != {"protocolVersion", "capabilities", "clientInfo"}:
                raise ValueError("Invalid MCP initialization")
            if not isinstance(params["capabilities"], dict) or not isinstance(
                params["clientInfo"], dict
            ):
                raise ValueError("Invalid MCP client metadata")
            if params["protocolVersion"] != "2025-06-18":
                raise ValueError("Supported MCP protocol version: 2025-06-18")
            self.negotiated = True
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "interview-answer-tools", "version": "1.0.0"},
            }
        elif method == "ping":
            if params:
                raise ValueError("Ping has no parameters")
            result = {}
        elif not self.ready:
            raise ValueError("MCP initialization is incomplete")
        elif method == "tools/list":
            if params:
                raise ValueError("Tool list has no pagination")
            result = {"tools": [FINISH_TOOL]}
        elif method == "tools/call":
            if set(params) != {"name", "arguments"} or params["name"] != FINISH_TOOL["name"]:
                raise ValueError("Unknown MCP tool or fields")
            args = params["arguments"]
            required = {"question_id", "answer_text", "completion_receipt"}
            if not isinstance(args, dict) or not required <= set(args):
                raise ValueError("Missing finish_current_answer arguments")
            if set(args) - required - {"progress_events"}:
                raise ValueError("Unknown finish_current_answer arguments")
            for name in required:
                if not isinstance(args[name], str) or not args[name].strip():
                    raise ValueError("Expected nonempty finish_current_answer strings")
            if type(args.get("progress_events", False)) is not bool:
                raise ValueError("Expected boolean progress_events")
            if str(UUID(identifier)) != identifier:
                raise ValueError("Tool call ID must be a canonical UUID string")
            verify_completion_receipt(
                args["completion_receipt"], self.owner_id, args["question_id"], args["answer_text"]
            )
            return None, {
                "type": "answer",
                "request_id": identifier,
                "question_id": args["question_id"],
                "answer_text": args["answer_text"],
                "progress_events": args.get("progress_events", False),
            }
        else:
            raise ValueError("Unknown MCP method")
        return {"jsonrpc": "2.0", "id": identifier, "result": result}, None
