# Deadlock AI Assistant API Integration Guide

**Version:** 1.0.0
**Base URL:** `https://your-deployment-url`

This document provides comprehensive documentation for third-party developers integrating with the Deadlock AI Assistant API.

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Endpoints](#endpoints)
   - [Health Check](#health-check)
   - [Chat](#chat)
4. [Request/Response Models](#requestresponse-models)
5. [Server-Sent Events (SSE)](#server-sent-events-sse)
6. [Error Handling](#error-handling)
7. [Status Codes](#status-codes)
8. [Integration Examples](#integration-examples)
9. [Best Practices](#best-practices)

---

## Overview

The Deadlock AI Assistant API provides a stateless REST interface for conversational AI interactions. Key features include:

- **Streaming Responses**: Real-time response streaming via Server-Sent Events (SSE)
- **Conversation Persistence**: Optional conversation history tracking via conversation IDs
- **Tool Integration**: AI can invoke tools during conversations with progress events
- **Multiple Authentication Methods**: API keys or Cloudflare Turnstile tokens

---

## Authentication

All endpoints except public paths require authentication. The API supports two authentication methods.

### Public Paths (No Authentication Required)

| Path | Description |
|------|-------------|
| `/health` | Health check endpoint |
| `/docs` | OpenAPI documentation (Swagger UI) |
| `/openapi.json` | OpenAPI schema |
| `/redoc` | ReDoc documentation |

### Method 1: API Key Authentication (Recommended)

Pass your API key in the `X-API-Key` header.

```http
POST /chat HTTP/1.1
Host: api.example.com
X-API-Key: your-api-key-here
Content-Type: application/json

{"message": "Hello, assistant!"}
```

### Method 2: Cloudflare Turnstile Token

For browser-based applications, pass a Turnstile token in the `cf-turnstile-response` header.

```http
POST /chat HTTP/1.1
Host: api.example.com
cf-turnstile-response: your-turnstile-token
Content-Type: application/json

{"message": "Hello, assistant!"}
```

### Authentication Precedence

1. If `X-API-Key` header is present, only API key validation is performed
2. If `cf-turnstile-response` header is present (and no API key), Turnstile validation is performed
3. If neither header is present, the request is rejected with 401 Unauthorized

### Authentication Failure Response

```json
{
  "error": "Invalid or missing API key",
  "code": "AUTH_FAILED",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

## Endpoints

### Health Check

Check API availability and service status.

**Endpoint:** `GET /health`
**Authentication:** None required

#### Response

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "services": {
    "redis": "connected"
  }
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | Always `"healthy"` if the API is running |
| `version` | `string` | API version number |
| `services.redis` | `string` | Redis status: `"connected"` or `"unavailable"` |

#### Notes

- Returns `200 OK` even if Redis is unavailable (API is still functional for some operations)
- Use this endpoint for load balancer health checks and monitoring

---

### Chat

Send a message and receive a streaming AI response.

**Endpoint:** `POST /chat`
**Authentication:** Required
**Content-Type:** `application/json`
**Response Content-Type:** `text/event-stream`

#### Request Body

```json
{
  "message": "What is the weather like today?",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

#### Request Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | `string` | Yes | The user's message to the assistant |
| `conversation_id` | `string \| null` | No | Existing conversation ID to continue a conversation |

#### Response

The response is a stream of Server-Sent Events. See [Server-Sent Events (SSE)](#server-sent-events-sse) for detailed event documentation.

#### Behavior

| Scenario | Behavior |
|----------|----------|
| No `conversation_id` provided | Creates a new conversation with a generated UUID |
| Valid `conversation_id` provided | Loads conversation history and continues the conversation |
| Invalid `conversation_id` provided | Starts a fresh conversation with the provided ID |

---

## Request/Response Models

### ChatRequest

```typescript
interface ChatRequest {
  message: string;              // User message (required)
  conversation_id?: string;     // Optional conversation ID
}
```

### ErrorResponse

All API errors return this consistent format:

```typescript
interface ErrorResponse {
  error: string;      // Human-readable error message
  code: string;       // Machine-readable error code
  request_id: string; // Unique request identifier for support
}
```

### HealthResponse

```typescript
interface HealthResponse {
  status: "healthy";
  version: string;
  services: {
    redis: "connected" | "unavailable";
  };
}
```

---

## Server-Sent Events (SSE)

The `/chat` endpoint returns a stream of Server-Sent Events. Each event follows the SSE format:

```
data: {"event": "...", ...}\n\n
```

### Event Types

#### ChatStartEvent

Sent when the response stream begins.

```json
{
  "event": "start",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `event` | `"start"` | Event type identifier |
| `conversation_id` | `string` | The conversation ID (new or existing) |

#### ChatDeltaEvent

Sent for each content chunk during streaming. Multiple delta events are sent as the response is generated.

```json
{
  "event": "delta",
  "content": "Hello! How can I "
}
```

| Field | Type | Description |
|-------|------|-------------|
| `event` | `"delta"` | Event type identifier |
| `content` | `string` | Partial response content |

#### ChatEndEvent

Sent when the response stream completes successfully.

```json
{
  "event": "end",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `event` | `"end"` | Event type identifier |
| `conversation_id` | `string` | The conversation ID |

#### ChatErrorEvent

Sent when an error occurs during streaming.

```json
{
  "event": "error",
  "error": "Failed to process request",
  "code": "AGENT_ERROR"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `event` | `"error"` | Event type identifier |
| `error` | `string` | Human-readable error message |
| `code` | `string` | Error code (see [Error Codes](#error-codes)) |

#### ChatToolStartEvent

Sent when the AI begins invoking a tool.

```json
{
  "event": "tool_start",
  "tool_name": "web_search",
  "arguments": {
    "query": "current weather"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `event` | `"tool_start"` | Event type identifier |
| `tool_name` | `string` | Name of the tool being invoked |
| `arguments` | `object` | Arguments passed to the tool |

#### ChatToolEndEvent

Sent when a tool invocation completes.

```json
{
  "event": "tool_end",
  "tool_name": "web_search",
  "success": true,
  "result_summary": "Found 5 weather results"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `event` | `"tool_end"` | Event type identifier |
| `tool_name` | `string` | Name of the tool that completed |
| `success` | `boolean` | Whether the tool execution succeeded |
| `result_summary` | `string` | Brief summary of the tool result |

### SSE Event Type Union

For TypeScript implementations:

```typescript
type SSEEvent =
  | ChatStartEvent
  | ChatDeltaEvent
  | ChatEndEvent
  | ChatErrorEvent
  | ChatToolStartEvent
  | ChatToolEndEvent;

interface ChatStartEvent {
  event: "start";
  conversation_id: string;
}

interface ChatDeltaEvent {
  event: "delta";
  content: string;
}

interface ChatEndEvent {
  event: "end";
  conversation_id: string;
}

interface ChatErrorEvent {
  event: "error";
  error: string;
  code: string;
}

interface ChatToolStartEvent {
  event: "tool_start";
  tool_name: string;
  arguments: Record<string, unknown>;
}

interface ChatToolEndEvent {
  event: "tool_end";
  tool_name: string;
  success: boolean;
  result_summary: string;
}
```

---

## Error Handling

### Error Codes

| Code | Description | Typical HTTP Status |
|------|-------------|---------------------|
| `AUTH_FAILED` | Authentication failed (invalid/missing API key or Turnstile token) | 401 |
| `VALIDATION_ERROR` | Request body validation failed | 400 |
| `AGENT_ERROR` | AI agent processing error | 500, 503 |
| `REDIS_ERROR` | Storage service unavailable | 503 |
| `INTERNAL_ERROR` | Unexpected server error | 500 |

### Error Response Format

All errors (both HTTP responses and SSE error events) use this structure:

```json
{
  "error": "Human-readable error message",
  "code": "ERROR_CODE",
  "request_id": "uuid-for-tracking"
}
```

### Agent Error Subtypes

The `AGENT_ERROR` code covers several underlying conditions:

| Condition | HTTP Status | Description |
|-----------|-------------|-------------|
| Authentication Error | 500 | AI provider authentication failed |
| Rate Limit Error | 503 | AI provider rate limit exceeded |
| Timeout Error | 503 | AI provider request timed out |
| Retry Exhausted Error | 503 | Maximum retry attempts reached |
| Configuration Error | 500 | AI agent misconfiguration |

---

## Status Codes

| Status Code | Meaning | When Used |
|-------------|---------|-----------|
| `200 OK` | Success | Successful chat/health requests |
| `400 Bad Request` | Validation Error | Invalid request body or parameters |
| `401 Unauthorized` | Authentication Failed | Missing or invalid API key/token |
| `500 Internal Server Error` | Server Error | Unexpected errors, agent configuration issues |
| `503 Service Unavailable` | Service Unavailable | Redis unavailable, rate limits, timeouts |

---

## Integration Examples

### JavaScript/TypeScript (Browser)

```typescript
async function chat(message: string, conversationId?: string): Promise<void> {
  const response = await fetch('https://api.example.com/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': 'your-api-key',
    },
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(`${error.code}: ${error.error}`);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const event = JSON.parse(line.slice(6));
        handleEvent(event);
      }
    }
  }
}

function handleEvent(event: SSEEvent): void {
  switch (event.event) {
    case 'start':
      console.log('Conversation ID:', event.conversation_id);
      break;
    case 'delta':
      process.stdout.write(event.content);
      break;
    case 'tool_start':
      console.log(`\n[Using tool: ${event.tool_name}]`);
      break;
    case 'tool_end':
      console.log(`[Tool ${event.tool_name}: ${event.success ? 'success' : 'failed'}]`);
      break;
    case 'end':
      console.log('\n[Complete]');
      break;
    case 'error':
      console.error(`Error (${event.code}): ${event.error}`);
      break;
  }
}
```

### Python

```python
import httpx
import json

def chat(message: str, conversation_id: str | None = None) -> None:
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": "your-api-key",
    }
    payload = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    with httpx.stream(
        "POST",
        "https://api.example.com/chat",
        headers=headers,
        json=payload,
    ) as response:
        response.raise_for_status()

        for line in response.iter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                handle_event(event)

def handle_event(event: dict) -> None:
    event_type = event["event"]

    if event_type == "start":
        print(f"Conversation ID: {event['conversation_id']}")
    elif event_type == "delta":
        print(event["content"], end="", flush=True)
    elif event_type == "tool_start":
        print(f"\n[Using tool: {event['tool_name']}]")
    elif event_type == "tool_end":
        status = "success" if event["success"] else "failed"
        print(f"[Tool {event['tool_name']}: {status}]")
    elif event_type == "end":
        print("\n[Complete]")
    elif event_type == "error":
        print(f"Error ({event['code']}): {event['error']}")
```

### cURL

```bash
# Health check
curl -X GET https://api.example.com/health

# Chat (streaming response)
curl -X POST https://api.example.com/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"message": "Hello, assistant!"}' \
  --no-buffer

# Continue existing conversation
curl -X POST https://api.example.com/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"message": "What was my last question?", "conversation_id": "550e8400-e29b-41d4-a716-446655440000"}' \
  --no-buffer
```

### Go

```go
package main

import (
    "bufio"
    "bytes"
    "encoding/json"
    "fmt"
    "net/http"
    "strings"
)

type ChatRequest struct {
    Message        string  `json:"message"`
    ConversationID *string `json:"conversation_id,omitempty"`
}

type SSEEvent struct {
    Event         string                 `json:"event"`
    Content       string                 `json:"content,omitempty"`
    ConversationID string                `json:"conversation_id,omitempty"`
    Error         string                 `json:"error,omitempty"`
    Code          string                 `json:"code,omitempty"`
    ToolName      string                 `json:"tool_name,omitempty"`
    Arguments     map[string]interface{} `json:"arguments,omitempty"`
    Success       bool                   `json:"success,omitempty"`
    ResultSummary string                 `json:"result_summary,omitempty"`
}

func chat(message string, conversationID *string) error {
    payload := ChatRequest{
        Message:        message,
        ConversationID: conversationID,
    }
    body, _ := json.Marshal(payload)

    req, _ := http.NewRequest("POST", "https://api.example.com/chat", bytes.NewBuffer(body))
    req.Header.Set("Content-Type", "application/json")
    req.Header.Set("X-API-Key", "your-api-key")

    client := &http.Client{}
    resp, err := client.Do(req)
    if err != nil {
        return err
    }
    defer resp.Body.Close()

    scanner := bufio.NewScanner(resp.Body)
    for scanner.Scan() {
        line := scanner.Text()
        if strings.HasPrefix(line, "data: ") {
            var event SSEEvent
            json.Unmarshal([]byte(line[6:]), &event)
            handleEvent(event)
        }
    }
    return nil
}

func handleEvent(event SSEEvent) {
    switch event.Event {
    case "start":
        fmt.Printf("Conversation ID: %s\n", event.ConversationID)
    case "delta":
        fmt.Print(event.Content)
    case "tool_start":
        fmt.Printf("\n[Using tool: %s]\n", event.ToolName)
    case "tool_end":
        status := "success"
        if !event.Success {
            status = "failed"
        }
        fmt.Printf("[Tool %s: %s]\n", event.ToolName, status)
    case "end":
        fmt.Println("\n[Complete]")
    case "error":
        fmt.Printf("Error (%s): %s\n", event.Code, event.Error)
    }
}
```

---

## Best Practices

### 1. Handle SSE Events Properly

- Buffer incoming data and split on `\n\n` to handle partial events
- Always check the `event` field before processing
- Handle all event types, including `error` events in the stream

### 2. Manage Conversations

- Store the `conversation_id` from the `start` event for multi-turn conversations
- Send the same `conversation_id` to continue a conversation
- Omit `conversation_id` to start a fresh conversation

### 3. Handle Errors Gracefully

- Check HTTP status codes before attempting to read the response body
- Handle both HTTP-level errors (non-200 responses) and SSE error events
- Use the `request_id` when contacting support for debugging

### 4. Implement Retry Logic

For transient errors (503 status codes), implement exponential backoff:

```typescript
async function chatWithRetry(
  message: string,
  maxRetries = 3
): Promise<void> {
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      await chat(message);
      return;
    } catch (error) {
      if (attempt === maxRetries - 1) throw error;
      if (error.code === 'REDIS_ERROR' || error.code === 'AGENT_ERROR') {
        await sleep(Math.pow(2, attempt) * 1000);
        continue;
      }
      throw error;
    }
  }
}
```

### 5. Security Considerations

- Never expose API keys in client-side code
- Use Cloudflare Turnstile for browser-based applications
- For server-to-server integrations, use API keys with appropriate access controls
- Rotate API keys periodically

### 6. Rate Limiting

- Implement client-side rate limiting to avoid hitting server limits
- Handle `503 Service Unavailable` responses with retry logic
- Monitor for `AGENT_ERROR` with rate limit conditions

---

## Response Headers

All responses include:

| Header | Description |
|--------|-------------|
| `X-Request-ID` | Unique identifier for the request (UUID) |

Use this ID when reporting issues or debugging.

---

## OpenAPI Specification

The full OpenAPI specification is available at:

- **Swagger UI:** `GET /docs`
- **OpenAPI JSON:** `GET /openapi.json`
- **ReDoc:** `GET /redoc`

---

## Changelog

### Version 1.0.0

- Initial API release
- Chat endpoint with SSE streaming
- Health check endpoint
- API key and Cloudflare Turnstile authentication
- Tool execution events

---

## Support

For API issues, include the following in your support request:

1. The `request_id` from the response header or error response
2. The error `code` received
3. Timestamp of the request
4. Endpoint and request payload (excluding sensitive data)

