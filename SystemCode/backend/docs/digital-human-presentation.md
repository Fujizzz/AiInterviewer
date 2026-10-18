# Digital-human facial presentation

The independent presentation Agent chooses facial behaviour for **Idle, Listening, Thinking and Speaking**. UE executes that behaviour continuously, replacing the recorded facial clips while a valid Agent plan is available. Recorded clips are the fallback for disabled planning, failures, expired plans or a manual release.

The service is outside `agents/`. It observes approved question text and existing presentation states. It cannot generate questions, select follow-ups, evaluate answers or change interview flow.

```text
Approved question or initial Idle context
                 ↓
       independent presentation Agent
                 ↓
   four bounded facial behaviour profiles
                 ↓
       UE continuous facial behaviour ← recorded fallback on failure
                 ↓
    blend with existing TTS-driven mouth animation
                 ↓
         Pixel Streaming → browser
```

## Configuration

The repository-root `.env` is the only environment file. The presentation service shares the selected provider's existing credentials and base URL.

| Variable | Meaning |
| --- | --- |
| `PRESENTATION_ENABLED` | Enable planning; disabled mode selects recorded fallback. |
| `PRESENTATION_PROVIDER` | `dashscope` or `openai`; blank inherits `LLM_PROVIDER`. |
| `PRESENTATION_MODEL` | Expression model; the example selects `qwen3.7-flash`. Blank inherits the selected provider's model. |
| `PRESENTATION_TIMEOUT_SECONDS` | Default 25 seconds, maximum 30; no retries. The browser's independent deadline is 35 seconds. |

Planning never gates TTS, answering or interview progress. A request supplies all four states; UE performs the frame-by-frame animation locally. There are no model requests for individual blinks, state changes, partial transcripts or cached-audio replay. The browser requests an initial Idle plan and a plan for each new approved question, and refreshes a model plan near its ten-minute expiry while connected and quiet during an active interview. Diagnostic pages require an explicit planning button.

The backend bounds concurrent calls and briefly caches results. Credentials remain in the backend. The browser reports `model`, `preview` or `fallback` with a short reason so local previews and failed model requests remain distinguishable.

## Presentation protocol

`POST /api/presentation/plan/` receives version `2`, a presentation-session UUID, a monotonic generation and either `question: null` or an approved question subset: `question_id`, `text`, and optional `question_type`, `intent`, `dialogue_action`. It accepts no candidate resume, answer, assessment or score. Initial Idle context uses `question: null` and returns `question_id: "session-idle"`.

The response echoes the correlation fields and includes `source`, `reason`, `transition_ms`, `valid_ms` and four profiles in `states`. Each profile contains:

| Fields | Purpose |
| --- | --- |
| `expression`, `intensity`, `variation` | A bounded neutral, attentive, thoughtful, friendly or emphasis expression with local variation. |
| `blink_min_ms`, `blink_max_ms`, `blink_duration_ms` | Irregular blink intervals and duration. |
| `gaze_amplitude`, `gaze_hold_min_ms`, `gaze_hold_max_ms`, `eye_contact` | Small eye movements and a preference for looking towards the camera. |
| `warmth` | Subtle closed-mouth warmth in quiet states. |
| `motion_min_ms`, `motion_max_ms` | Timing of small expression changes. |
| `head_motion_strength`, `head_motion_probability` | Bounded strength and likelihood of local head gestures, using the existing motion intervals. |
| `audio_emphasis_strength` | Subtle Speaking head/brow emphasis derived from the locally playing TTS waveform. |

Profile values are strictly validated and bounded. No model-provided bone names, raw curves or executable commands are accepted. The browser sends the sanitized plan as `expression_plan` over the existing Pixel Streaming channel. The attached `utterance_id` correlates playback; it does not start audio. `expression_clear` releases the plan. Cancelled requests and old generations cannot take ownership of a new question.

The three head/audio tendency fields are optional for version-two compatibility and default to zero in older plans. New model plans and local preview profiles include them. Each accepts a finite number from zero to one; UE applies its own small angular and timing limits.

Model plans are valid for ten minutes. Rebinding an utterance or reconnecting transmits the remaining lifetime and cannot renew an expired result. A local preview uses `source: "preview"`; it makes no model call. `source: "fallback"` selects recorded animation regardless of the supplied profiles.

## Facial ownership and blending

While healthy, the Agent plan supplies the whole quiet facial expression: UE generates the blink, gaze, brow variation and gentle resting mouth expression. The recorded facial clip is excluded from that output. This is continuous local behaviour driven by a model-selected profile, rather than a model generating one facial landmark or curve every frame.

During Speaking, Agent behaviour supplies the upper face, blink and gaze. The existing audio solver continues to supply jaw and lip movement. Quiet-state mouth warmth is excluded during speech. Normal completion blends the last visible mouth and facial expression into the next quiet state over 0.8 seconds; entry, interruption and failure retain the 0.3-second boundary blend. Audio finishes immediately, and ordinary speech mouth movement remains unfiltered. These durations are adjustable on the UE interview controller and are fixed at each transition boundary.

Model arrival, replacement, expiry and failure blend into the corresponding output. The body retains its common Idle animation and original post-process. A presentation node after the existing Body graph adds small, unified neck/head gestures. Face copies the resulting Body pose, preserving the existing head-control boundary; it is not rotated independently or replaced on each state change.

## Local attention and speech rhythm

Listening nods are occasional attention gestures, never an assessment of an answer. The browser observes energy from the PCM chunks it already captures for STT. It sends only capture identity, monotonic lifecycle/sequence counters, and active/ended flags; no audio, transcript or candidate information enters the expression service. After a voiced interval and a short pause, UE can schedule a small nod and keep eye contact. Initial silence, ended capture and expired activity cannot trigger a nod. The existing answer clocks and submission boundaries are unchanged.

`listening_activity` uses `presentation_id`, `capture_id`, `capture_generation`, `sequence`, `active` and `ended`. Capture generation is independent of expression-plan generation, so a profile refresh does not reset microphone observation. The browser debounces quiet for 450 ms and sends voiced heartbeats at most once per second. UE expires stale activity after 1.8 seconds and rejects old captures, sequences and ended-capture resurrection. Disconnect, capture failure and cancellation close the observation lifecycle.

Thinking uses an occasional restrained head tilt with a short gaze shift and return. Speaking samples the actual audible PCM timeline locally for gentle head/brow emphasis, retaining the solver's exclusive lip/jaw ownership. No gesture triggers a provider request. The same local state controller blends gestures out on state changes, plan expiry or recorded fallback.

## Manual preview

Open `/stream-demo/digital-human-check.html` and connect the avatar. Connecting this diagnostic page makes no model request.

1. Choose an expression and strength, then click **Preview local behaviour**. This replaces recorded facial clips using local sample profiles. Observe at least two blink cycles rather than judging a single frame.
2. Switch between Idle, Listening and Thinking using **Apply state**. Thinking gestures are sparse, so allow several motion windows.
3. Click **Preview listening rhythm** to simulate several speech/pause intervals for 17 seconds. It uses no microphone, STT or model call. Nods remain probabilistic; compare a few runs instead of expecting a nod at every pause.
4. Click **Use recorded fallback** to compare with the existing clips.
5. Click **Plan expressions for test question** to request one real Agent plan for the existing text. The status identifies model success or fallback.
6. Play or replay a short question to check Speaking, its audio emphasis and its entry/exit transitions. Replay uses the existing plan and cached audio; synthesis and transcription are separate explicit model calls.

Transport failures show a bounded reason: `http_401`/`http_403` identify access or CSRF rejection, `network_error` identifies a browser/network failure, and `invalid_json` identifies an unreadable service response. The default browser fetch is bound to `globalThis`; otherwise a native browser can reject the invocation before sending HTTP.

Provider fallback reasons distinguish `provider_timeout`, authentication, quota exhaustion, rate limiting, model access, invalid requests and connection errors. Logs retain only fixed diagnostic codes, exception type and numeric status; provider bodies and credentials are excluded. A blank `PRESENTATION_MODEL` inherits the selected provider's configured model and does not mean that no model is configured.

The `/agent/` page observes existing question, state and playback events. A plan remains available while another module processes an answer. Returning to Idle can reuse a still-valid profile without renewing its expiry. Voice-only operation needs no presentation-model request.

## Validation

Provider and browser tests use mocks. Native automation uses bounded sample profiles and the saved English WAV, without billable model calls. Checks cover strict profile validation, cancellation, plan lifetime, primary/fallback ownership, local blink/gaze variation, audio mouth priority, and speech entry/completion/interruption.

Automated checks establish interface and animation ownership behaviour. The user's browser preview establishes whether the expression intensity, eye contact and local variation look natural on the selected character.

On 2026-10-04, 30 backend speech/presentation tests, 32 browser speech/presentation tests, 27 existing interview-client tests and 14 packaged UE checks passed offline. An isolated headless Chrome check also verified the actual built controller with native Window fetch, four-state plan acceptance and safe HTTP 403 fallback, using local mock responses without model calls. The packaged cached-WAV check observed 105 solver frames and 251 speech curves; 99 jaw samples had zero deviation after primary Agent face control was enabled. Completion and interruption preserved the Face/Body instances and head-control boundary. Native results are saved in `DigitalHuman/Saved/FacialAgentValidation/Automation/index.json`. These checks did not call the expression model or assess subjective appearance.

A subsequent live-provider investigation reproduced a read timeout with the former six-second limit. With a 25-second limit, the configured `qwen3.7-plus` returned a fully validated four-state plan in 13.1 seconds. The provider classifier and timeout change passed 14 targeted offline backend tests, and the independent 35-second browser deadline passed the client tests. No speech or interview-question requests were needed for this investigation.

The attention/rhythm update passed 32 backend speech/presentation tests, 66 client tests and 18 packaged UE checks on 2026-10-04. The Body graph was backed up before appending its native head node. The cached-WAV run confirmed that the node evaluated, actual PCM supplied speech rhythm, and Face/Body neck/head rotation differed by at most 0.000002 degrees. Across 99 jaw samples, maximum deviation from the native speech solver remained zero. Completion and interruption retained both animation instances. The final report is `DigitalHuman/Saved/AttentionMotionValidation/Automation/index.json`; these checks used local fixtures without provider calls. Visual naturalness remains a manual preview check.
