#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy 云端每日签到（GitHub Actions 用，自包含·纯标准库）

从环境变量 WORKBUDDY_REFRESH_TOKEN 读长效令牌 -> 换新 accessToken -> 签到 + 派猫猫旅行。
不读任何本地文件，不含任何硬编码凭证，输出脱敏。
"""
import base64, json, os, re, sys, urllib.request, urllib.error

REFRESH_URL = "https://copilot.tencent.com/v2/plugin/auth/token/refresh"
CHECKIN_STATUS = "/v2/billing/meter/checkin-activity-status"
CHECKIN = "/v2/billing/meter/daily-checkin"
TRAVEL_STATUS = "/activity/growth/buddy/travel/status"
TRAVEL_CLAIM = "/activity/growth/buddy/travel/claim"
TRAVEL_DEPART = "/activity/growth/buddy/travel/depart"


def api(domain, token, path, method="POST", payload=None):
    url = "https://" + domain + path
    body = json.dumps(payload or {}, ensure_ascii=False).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {"code": -1, "msg": "HTTP " + str(e.code)}
    except Exception as e:
        return 0, {"code": -1, "msg": str(e)}


def renew(rt):
    req = urllib.request.Request(REFRESH_URL, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Refresh-Token", rt)
    req.add_header("X-Auth-Refresh-Source", "plugin")
    req.add_header("X-Domain", "copilot.tencent.com")
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    if d.get("code") != 0 or not isinstance(d.get("data"), dict):
        raise RuntimeError("refresh 失败：%s" % d.get("msg"))
    return d["data"].get("accessToken", "")


def domain_of(token):
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        iss = json.loads(base64.urlsafe_b64decode(p)).get("iss", "")
        m = re.match(r"https?://([^/]+)", iss)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "www.workbuddy.cn"


def checkin(domain, token):
    st, b = api(domain, token, CHECKIN_STATUS)
    if st == 200 and b.get("code") == 0 and (b.get("data") or {}).get("today_checked_in"):
        return "今日已签到（跳过）"
    st, b = api(domain, token, CHECKIN)
    if st == 200 and b.get("code") == 0:
        d = b.get("data") or {}
        return "签到成功 +%s分，连续%s天，余额%s" % (
            d.get("credit", "?"), d.get("streak_days", "?"), d.get("total_credits", "?"))
    if b.get("code") == 10001:
        return "今日已签到（幂等）"
    return "签到异常：%s" % b.get("msg")


def travel(domain, token):
    st, b = api(domain, token, TRAVEL_STATUS, method="GET")
    if st != 200 or b.get("code") != 0:
        return "旅行状态查询失败"
    s = b.get("data") or {}
    out = []
    if s.get("state") == "arrived":
        _, c = api(domain, token, TRAVEL_CLAIM, payload={})
        if c.get("code") == 0:
            cr = (c.get("data") or {}).get("reward_credit")
            out.append("已领旅行奖励 +%s分" % (cr if cr is not None else "?"))
            _, b2 = api(domain, token, TRAVEL_STATUS, method="GET")
            s = (b2.get("data") or {}) if b2.get("code") == 0 else s
    if s.get("state") in (None, "", "idle"):
        if s.get("daily_limit_reached"):
            out.append("今日已派猫（跳过）")
        else:
            _, d = api(domain, token, TRAVEL_DEPART, payload={"location_id": 1})
            out.append("已派猫旅行" if d.get("code") == 0 else "派猫失败：%s" % d.get("msg"))
    else:
        out.append("猫猫旅行中，下次运行自动领奖")
    return "；".join(out) or "无操作"


def main():
    rt = os.environ.get("WORKBUDDY_REFRESH_TOKEN", "").strip()
    if not rt:
        raise RuntimeError("缺少环境变量 WORKBUDDY_REFRESH_TOKEN")
    at = renew(rt)
    domain = domain_of(at)
    print("令牌续期成功，域名：%s" % domain)
    print(checkin(domain, at))
    print(travel(domain, at))
    print("DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR: %s" % e)
        sys.exit(1)
