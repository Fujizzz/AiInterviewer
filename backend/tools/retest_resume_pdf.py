"""Responsibilities: Explicitly retest the local PDF extraction and vision pipelines with bounded
diagnostics and private result files.
Implementation: Default to traditional extraction; advanced mode applies the current line-number
protocol and strict range validation while preserving configured model parameters.
Related Modules: interviews.pdf_sandbox, interviews.resume_vision, and agents.resume_cleanup; real
models are called only during direct execution.
Declaration Index:
- ObservedVision: Wrap the existing vision client and retain the last page review for bounded
  diagnostics.
- ObservedVision.__init__: Initialize the real client and empty observation state.
- ObservedVision.review: Delegate review and retain the returned structure without rewriting it.
- retest: Run the selected local pipeline and save successful output and bounded failure details.
- retest_http: Call the local HTTP parse endpoint after obtaining CSRF and save its result
  privately.
- main: Validate CLI arguments, initialize Django, and run one explicit live retest.
Variable Index:
None
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


class ObservedVision:
    """Functionality: Wrap the production vision adapter to observe the last review result.
    Inputs: No explicit inputs; the client reads its existing environment configuration.
    Outputs: Stores the client and initially empty result state.
    Logic: Construct ResumeVision without changing its configuration.
    Constraints: The caller is responsible for closing the client.
    """

    def __init__(self):
        """Functionality: Initialize the real vision adapter and observation state.
        Inputs: No explicit arguments beyond the instance.
        Outputs: Sets client and result attributes.
        Logic: Construct ResumeVision and initialize result to None.
        Constraints: Environment configuration is read by the existing adapter; client cleanup is
        external.
        """
        from interviews.resume_vision import ResumeVision

        self.client = ResumeVision()
        self.result = None

    async def review(self, page, prompt):
        """Functionality: Delegate one page review and retain the original response for diagnostics.
        Inputs: A page and review prompt.
        Outputs: The original PageReview returned by the client.
        Logic: Clear previous observation, await the client, save and return its result.
        Constraints: Model exceptions propagate; neither input nor output is rewritten.
        """
        self.result = None
        self.result = await self.client.review(page, prompt)
        return self.result


async def retest(source, output, *, mode="traditional"):
    """Functionality: Run the selected local PDF pipeline and persist its successful results.
    Inputs: Source PDF path, private output directory, and extraction mode.
    Outputs: True on a complete pipeline and False on an advanced-mode page failure.
    Logic: Parse the PDF; traditional mode serializes rule output, while advanced mode records page
    images and validates model corrections.
    Constraints: Diagnostics omit source text, provider exceptions, and secrets; the real vision
    client is closed in a finally block.
    """
    from interviews.pdf_sandbox import parse_pdf
    from interviews.resume_api import resume_failure_code

    from agents.resume_cleanup import ResumeCleanupAgent, replacement_text, source_lines

    pages = await parse_pdf(source.read_bytes())
    output.mkdir(parents=True, exist_ok=True)
    print(f"rules_completed pages={len(pages)}", flush=True)
    if mode == "traditional":
        results = [
            {
                "number": page.number,
                "text": page.text,
                "changed": False,
                "uncertainties": page.warnings,
            }
            for page in pages
        ]
        (output / "result.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("pipeline_completed mode=traditional", flush=True)
        return True
    vision = ObservedVision()
    results = []
    try:
        for page in pages:
            (output / f"page-{page.number}.png").write_bytes(page.image_png)
            try:
                result = await ResumeCleanupAgent(vision).clean_page(page)
            except Exception as exc:
                print(
                    f"pipeline_failed page={page.number} code={resume_failure_code(exc)} "
                    f"exception={type(exc).__name__}",
                    flush=True,
                )
                if vision.result is not None:
                    lines = source_lines(page.text)
                    for index, item in enumerate(vision.result.corrections):
                        valid = 1 <= item.start_line <= item.end_line <= len(lines)
                        same = valid and "".join(
                            lines[item.start_line - 1 : item.end_line]
                        ) == replacement_text(item, lines)
                        print(
                            json.dumps(
                                {
                                    "correction_index": index,
                                    "start_line": item.start_line,
                                    "end_line": item.end_line,
                                    "range_valid": valid,
                                    "replacement_chars": len(item.text),
                                    "same_text": same,
                                    "reason_blank": not item.reason.strip(),
                                }
                            ),
                            flush=True,
                        )
                return False
            results.append(result.model_dump(mode="json"))
            print(
                f"page_completed page={page.number} changed={result.changed} "
                f"corrections={len(result.corrections)} uncertainties={len(result.uncertainties)}",
                flush=True,
            )
        (output / "result.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("pipeline_completed", flush=True)
        return True
    finally:
        await vision.client.close()


async def retest_http(source, output, url, *, mode="traditional"):
    """Functionality: Exercise the local HTTP resume-parse route and persist its terminal result.
    Inputs: PDF path, private output directory, backend URL, and extraction mode.
    Outputs: True only after a result event; setup, HTTP, error-event, or missing-terminal failures
    return False.
    Logic: Fetch the agent page for CSRF state, submit the PDF, consume NDJSON, and write the result
    event.
    Constraints: The agent page must allow anonymous access; no session is created or bypassed,
    HTTP retries are disabled, server-side JSON retries follow service configuration, and only
    bounded event metadata is printed.
    """
    import httpx

    output.mkdir(parents=True, exist_ok=True)
    completed = False
    async with httpx.AsyncClient(timeout=180, follow_redirects=False) as client:
        page = await client.get(url + "/agent/")
        if page.status_code != 200:
            print(f"http_setup_failed status={page.status_code}", flush=True)
            return False
        headers = {
            "Origin": url,
            "Referer": url + "/agent/",
            "X-CSRFToken": client.cookies.get("csrftoken", ""),
        }
        async with client.stream(
            "POST",
            url + "/api/resume/parse/",
            files={"file": ("resume.pdf", source.read_bytes(), "application/pdf")},
            data={"mode": mode},
            headers=headers,
        ) as response:
            if response.status_code != 200:
                print(f"http_failed status={response.status_code}", flush=True)
                return False
            async for line in response.aiter_lines():
                if not line:
                    continue
                event = json.loads(line)
                print(
                    f"http_event type={event['type']} stage={event.get('stage', '')} "
                    f"code={event.get('code', '')}",
                    flush=True,
                )
                if event["type"] == "error":
                    return False
                if event["type"] == "result":
                    (output / "http-result.json").write_text(
                        json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    completed = True
    print(f"http_completed success={completed}", flush=True)
    return completed


def main():
    """Functionality: Parse CLI options and run one explicit live pipeline retest.
    Inputs: Source/output paths, optional local HTTP URL, extraction mode, and process environment.
    Outputs: Process status 0 for success and 1 for a reported pipeline failure.
    Logic: Add the backend import path, initialize Django, select the requested operation, and
    execute it once.
    Constraints: The default mode is traditional; advanced mode reuses configured JSON retries.
    The CLI does not rerun the document or adjust model parameters.
    """
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    parser = argparse.ArgumentParser(
        description="Explicit live PDF pipeline retest using configured JSON validation retries."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--http-url", help="Explicit local backend URL for real HTTP retest.")
    parser.add_argument("--mode", choices=["traditional", "advanced"], default="traditional")
    args = parser.parse_args()
    operation = (
        retest_http(args.source, args.output, args.http_url.rstrip("/"), mode=args.mode)
        if args.http_url
        else retest(args.source, args.output, mode=args.mode)
    )
    return 0 if asyncio.run(operation) else 1


if __name__ == "__main__":
    raise SystemExit(main())
