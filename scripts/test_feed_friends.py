#!/usr/bin/env python3
"""验证方案二：Cookie 抓取微博关注首页流 + 长微博全文补拉。

实测结论（2026-10-11）：
- 桌面版 cookie（weibo.com 登录）走 weibo.com/ajax/* 接口可用；
  m.weibo.cn 移动端接口需要 m.weibo.cn 自己的登录态，桌面 cookie 不通（ok=-100）。
- 关注流: GET /ajax/feed/friendstimeline  （since_id / max_id 双向翻页）
- 单条/长微博全文: GET /ajax/statuses/show?id=<bid>&isGetLongText=true

用法:
    python3 scripts/test_feed_friends.py [cookie文件路径] [--pages N]

默认读取项目根目录 cookie.txt（一整行 Cookie 头内容），默认抓 2 页验证翻页。
"""
import argparse
import html
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COOKIE_FILE = ROOT / "cookie.txt"

DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

session = requests.Session()
session.headers.update(
    {
        "User-Agent": DESKTOP_UA,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://weibo.com/",
    }
)


def load_cookie(path: Path) -> None:
    if not path.exists():
        sys.exit(f"Cookie 文件不存在: {path}")
    cookie = path.read_text(encoding="utf-8").strip()
    session.headers["Cookie"] = cookie
    m = re.search(r"(?:^|;\s*)XSRF-TOKEN=([^;]+)", cookie)
    if m:
        session.headers["X-Xsrf-Token"] = m.group(1)


def check_resp(resp: requests.Response, name: str) -> dict:
    """统一错误检查: 432=风控, ok!=1=异常（含未登录）。"""
    if resp.status_code == 432:
        sys.exit(f"[{name}] HTTP 432: 触发风控/限流，请降速后重试")
    if resp.status_code in (401, 403, 302):
        sys.exit(f"[{name}] HTTP {resp.status_code}: Cookie 失效或未登录，请重新获取")
    resp.raise_for_status()
    data = resp.json()
    if data.get("ok") != 1:
        sys.exit(f"[{name}] ok={data.get('ok')}: 接口异常，可能是 Cookie 失效。原始返回: {str(data)[:200]}")
    return data


def strip_html(text: str) -> str:
    """粗略去 HTML 标签，用于终端预览。"""
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def fetch_friends_feed(max_id: str = None, count: int = 25) -> dict:
    params = {"count": str(count)}
    if max_id:
        params["max_id"] = max_id
    resp = session.get(
        "https://weibo.com/ajax/feed/friendstimeline", params=params, timeout=15
    )
    return check_resp(resp, "feed/friendstimeline")


def fetch_full_text(bid: str) -> str:
    """长微博补拉全文（isGetLongText=true），返回纯文本。"""
    resp = session.get(
        "https://weibo.com/ajax/statuses/show",
        params={"id": bid, "isGetLongText": "true"},
        timeout=15,
    )
    data = check_resp(resp, "statuses/show")
    return strip_html(data.get("text", ""))


def print_status(i: int, s: dict) -> None:
    user = s.get("user") or {}
    preview = strip_html(s.get("text", "")).replace("\n", " ")[:80]
    print(f"[{i:02d}] {user.get('screen_name', '?')} @ {s.get('created_at', '?')}")
    print(
        f"     id={s.get('idstr')} bid={s.get('mblogid')} "
        f"长微博={bool(s.get('isLongText'))} 转发={'retweeted_status' in s} "
        f"图片数={len(s.get('pic_ids') or [])}"
    )
    print(f"     {preview}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cookie", nargs="?", default=str(COOKIE_FILE))
    parser.add_argument("--pages", type=int, default=2)
    args = parser.parse_args()
    load_cookie(Path(args.cookie))

    seen_ids = set()
    long_text_candidate = None
    max_id = None
    for page in range(1, args.pages + 1):
        data = fetch_friends_feed(max_id=max_id)
        statuses = data.get("statuses", [])
        new_ids = {s["idstr"] for s in statuses}
        print(
            f"== 第 {page} 页: {len(statuses)} 条, 与前面重复 {len(seen_ids & new_ids)} 条, "
            f"since_id={data.get('since_id_str')} max_id={data.get('max_id_str')} ==\n"
        )
        seen_ids |= new_ids
        for idx, s in enumerate(statuses, 1):
            print_status(idx, s)
        if long_text_candidate is None:
            long_text_candidate = next((s for s in statuses if s.get("isLongText")), None)
        max_id = data.get("max_id_str")
        if not max_id or not statuses:
            break
        if page < args.pages:
            time.sleep(2)  # 翻页间隔，防风控

    print(f"\n共抓取 {len(seen_ids)} 条不重复微博")

    print("\n== 验证长微博全文补拉 ==")
    if not long_text_candidate:
        print("这几页没有长微博，可加 --pages 再试")
        return
    bid = long_text_candidate.get("mblogid")
    full = fetch_full_text(bid)
    truncated_len = len(strip_html(long_text_candidate.get("text", "")))
    print(f"bid={bid} 截断正文 {truncated_len} 字 -> 全文 {len(full)} 字")
    print(f"全文开头: {full[:120]}")
    print(f"全文结尾: ...{full[-120:]}")


if __name__ == "__main__":
    main()
