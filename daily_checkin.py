#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy 云端每日签到（GitHub Actions 用，自包含·纯标准库）

从环境变量 WORKBUDDY_REFRESH_TOKEN 读长效令牌 -> 换新 accessToken -> 签到 + 派猫猫旅行。
不读任何本地文件，不含任何硬编码凭证，输出脱敏。
增强诊断：令牌失效时直接打印原因和解决步骤；令牌临期提前预警。
"""
import base64, datetime, json, os, re, sys, urllib.request, urllib.error

REFRESH_URL = "https://copilot.tencent.com/v2/plugin/auth/token/refresh"
CHECKIN_STATUS = "/v2/billing/meter/checkin-activity-status"
CHECKIN = "/v2/billing/meter/daily-checkin"
TRAVEL_STATUS = "/activity/growth/buddy/travel/status"
TRAVEL_CLAIM = "/activity/growth/buddy/travel/claim"
TRAVEL_DEPART = "/activity/growth/buddy/travel/depart"

# 失效时的指引（出现 revoked / invalid_grant 时打印）
REVOKED_HINT = (
    "【令牌已失效·不用慌】原因几乎都是：你重新登录了 WorkBuddy 客户端，"
    "新登录会作废旧令牌（平台安全机制）。\n"
    "  解决办法：让 WorkBuddy 重新导出一次新令牌 → 更新本仓库 "
    "Settings → Secrets → actions → WORKBUDDY_REFRESH_TOKEN 的值 → 手动重跑本工作流。"
)


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


def token_exp_info(rt):
    """解析 refresh token 的 exp，返回 (到期日期字符串, 剩余天数)；失败返回 (None, None)。"""
    try:
        payload = rt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        d = json.loads(base64.urlsafe_b64decode(payload))
        exp = d.get("exp", 0)
        if not exp:
            return None, None
        dt = datetime.datetime.fromtimestamp(exp)
        days = (dt - datetime.datetime.now()).days
        return dt.strftime("%Y-%m-%d"), days
    except Exception:
        return None, None


def renew(rt):
    req = urllib.request.Request(REFRESH_URL, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Refresh-Token", rt)
    req.add_header("X-Auth-Refresh-Source", "plugin")
    req.add_header("X-Domain", "copilot.tencent.com")
    def parse_msg(obj, raw):
        """从响应体提取 msg，并判断是否属于令牌失效。失效则抛出友好提示。"""
        msg = str(obj.get("msg", "")) if isinstance(obj, dict) else raw
        low = msg.lower()
        if "revoked" in low or "invalid_grant" in low or "invalid grant" in low:
            raise RuntimeError(REVOKED_HINT + "\n  原始错误：" + msg[:150])
        return msg

    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
        msg = parse_msg(d, raw)
        raise RuntimeError("令牌续期返回 HTTP %d：%s" % (e.code, msg[:150]))
    except Exception as e:
        raise RuntimeError("令牌续期网络失败（GitHub 到腾讯接口不通）：%s" % e)
    if d.get("code") != 0 or not isinstance(d.get("data"), dict):
        msg = parse_msg(d, raw)
        raise RuntimeError("令牌续期失败：%s" % msg[:150])
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
        raise RuntimeError("【缺少密钥】没有读到 WORKBUDDY_REFRESH_TOKEN，"
                           "请在仓库 Settings → Secrets → actions 里添加该 Secret。")
    exp_str, days = token_exp_info(rt)
    if exp_str:
        print("令牌有效期至 %s（剩余 %d 天）" % (exp_str, days))
        if days <= 7:
            print("【临期预警】令牌 7 天内到期，请尽快重新导出并更新 Secret。")
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
