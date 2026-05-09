from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from typing import Any
from urllib import error, request


def _run_git(args: list[str]) -> str:
    proc = subprocess.run(args, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Git command failed: {' '.join(args)}\n{proc.stderr.strip()}")
    return proc.stdout


def _normalize_org_url(org: str) -> str:
    if org.startswith("http://") or org.startswith("https://"):
        return org.rstrip("/")
    return f"https://dev.azure.com/{org}"


def _auth_headers(token: str) -> dict[str, str]:
    basic = base64.b64encode(f":{token}".encode()).decode("ascii")
    return {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/json",
    }


def _ado_request(url: str, token: str, method: str = "GET", data: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = request.Request(url=url, method=method, data=body, headers=_auth_headers(token))
    with request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _get_diff_from_git(max_chars: int) -> tuple[list[str], str]:
    source = os.getenv("SYSTEM_PULLREQUEST_SOURCEBRANCH", "")
    target = os.getenv("SYSTEM_PULLREQUEST_TARGETBRANCH", "")

    if not source or not target:
        diff = _run_git(["git", "diff", "--no-color", "--unified=3", "HEAD~1", "HEAD"])
        files = [p for p in _run_git(["git", "diff", "--name-only", "HEAD~1", "HEAD"]).splitlines() if p]
        return files, diff[:max_chars]

    source_local = "refs/heads/pr_source_branch"
    target_local = "refs/heads/pr_target_branch"
    _run_git(["git", "fetch", "origin", f"{source}:{source_local}", f"{target}:{target_local}"])

    range_expr = f"{target_local}...{source_local}"
    files = [p for p in _run_git(["git", "diff", "--name-only", range_expr]).splitlines() if p]
    diff = _run_git(["git", "diff", "--no-color", "--unified=3", range_expr])
    return files, diff[:max_chars]


def _call_model(model_endpoint: str, api_key: str, prompt: str) -> str:
    payload = {
        "temperature": 0.1,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a senior code reviewer. Return strict JSON with keys: "
                    "summary (string) and findings (array). "
                    "Each finding must contain title, severity, path, line, and comment."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }

    req = request.Request(
        url=model_endpoint,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "api-key": api_key,
        },
    )

    with request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")

    obj = json.loads(raw)

    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content

    output = obj.get("output_text")
    if isinstance(output, str):
        return output

    return json.dumps(obj)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1) if match else text


def _parse_review_output(review_text: str) -> dict[str, Any]:
    clean = _strip_json_fence(review_text)
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    return {"summary": review_text, "findings": []}


def _post_general_thread(api_base: str, pr_id: int, token: str, content: str) -> None:
    url = f"{api_base}/pullRequests/{pr_id}/threads?api-version=7.1"
    payload = {
        "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}],
        "status": "active",
    }
    _ado_request(url=url, token=token, method="POST", data=payload)


def _post_file_thread(api_base: str, pr_id: int, token: str, path: str, line: int, content: str) -> None:
    if not path.startswith("/"):
        path = f"/{path}"

    url = f"{api_base}/pullRequests/{pr_id}/threads?api-version=7.1"
    payload = {
        "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}],
        "status": "active",
        "threadContext": {
            "filePath": path,
            "rightFileStart": {"line": max(line, 1), "offset": 1},
            "rightFileEnd": {"line": max(line, 1), "offset": 1},
        },
    }
    _ado_request(url=url, token=token, method="POST", data=payload)


def _build_prompt(pr_title: str, pr_description: str, files: list[str], diff: str) -> str:
    files_preview = "\n".join(f"- {path}" for path in files[:100])
    return (
        "Review this pull request and produce JSON only.\n\n"
        f"PR title:\n{pr_title}\n\n"
        f"PR description:\n{pr_description}\n\n"
        f"Changed files:\n{files_preview}\n\n"
        "Unified diff:\n"
        f"{diff}\n\n"
        "Return JSON schema: "
        '{"summary":"...","findings":[{"title":"...","severity":"low|medium|high","path":"...","line":1,"comment":"..."}]}'
    )


def _parse_line_number(line_raw: Any) -> int:
    try:
        line = int(line_raw)
    except TypeError:
        return 1
    except ValueError:
        return 1
    return line if line > 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="AI review for Azure DevOps pull requests")
    parser.add_argument("--org", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr-id", type=int, default=0)
    parser.add_argument("--model-endpoint", required=True)
    parser.add_argument("--model-key-env", default="MODEL_API_KEY")
    parser.add_argument("--max-diff-chars", type=int, default=120000)
    args = parser.parse_args()

    token = os.getenv("SYSTEM_ACCESSTOKEN", "")
    if not token:
        print(
            "Error: SYSTEM_ACCESSTOKEN environment variable is required for Azure DevOps authentication",
            file=sys.stderr,
        )
        return 2

    model_api_key = os.getenv(args.model_key_env, "")
    if not model_api_key:
        print(
            f"Error: {args.model_key_env} environment variable is required for model API authentication",
            file=sys.stderr,
        )
        return 2

    pr_id = args.pr_id or int(os.getenv("SYSTEM_PULLREQUEST_PULLREQUESTID", "0"))
    if pr_id <= 0:
        print("No PR context detected; skipping AI review")
        return 0

    org_url = _normalize_org_url(args.org)
    api_base = f"{org_url}/{args.project}/_apis/git/repositories/{args.repo}"

    try:
        pr_obj = _ado_request(
            url=f"{api_base}/pullRequests/{pr_id}?api-version=7.1",
            token=token,
        )
        pr_title = str(pr_obj.get("title", ""))
        pr_description = str(pr_obj.get("description", ""))

        files, diff = _get_diff_from_git(max_chars=args.max_diff_chars)
        if not files:
            print("No changed files detected; skipping AI review")
            return 0

        prompt = _build_prompt(pr_title=pr_title, pr_description=pr_description, files=files, diff=diff)
        review_text = _call_model(model_endpoint=args.model_endpoint, api_key=model_api_key, prompt=prompt)
        review = _parse_review_output(review_text)

        summary = str(review.get("summary", "AI review completed."))
        findings = review.get("findings", [])
        if not isinstance(findings, list):
            findings = []

        summary_comment = f"## 🤖 AI PR Review Summary\n\n{summary}\n\nFiles reviewed: {len(files)}"
        _post_general_thread(api_base=api_base, pr_id=pr_id, token=token, content=summary_comment)

        for finding in findings[:20]:
            if not isinstance(finding, dict):
                continue

            title = str(finding.get("title", "Review finding")).strip() or "Review finding"
            severity = str(finding.get("severity", "medium")).strip().lower()
            path = str(finding.get("path", "")).strip()
            line = _parse_line_number(finding.get("line", 1))
            comment = str(finding.get("comment", "")).strip() or "No detail provided."

            body = f"**[{severity}] {title}**\n\n{comment}"
            if path:
                _post_file_thread(api_base=api_base, pr_id=pr_id, token=token, path=path, line=line, content=body)
            else:
                _post_general_thread(api_base=api_base, pr_id=pr_id, token=token, content=body)

        print("AI review comments posted successfully")
        return 0
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP error: {exc.code} {details}", file=sys.stderr)
        return 1
    except error.URLError as exc:
        # Catches non-HTTP network failures (e.g., DNS resolution, connection timeout).
        print(f"Network error while contacting Azure DevOps or model endpoint: {exc.reason}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"AI review failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
