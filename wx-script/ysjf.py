#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# name: 影视飓风签到
# cron: 20 8 * * *
#
# 环境变量：
#   YYB_SERVER   每行：地址@账号标识（例如 http://yyb-go:8000@1）
#   YSJF_NOTIFY  0 关闭青龙通知；默认 1
#
# 业务接口：影视飓风小程序（有赞微商城平台）uic.youzan.com / h5.youzan.com
# 流程：微信 code 换 access_token → create-client 建会话 → 查签到活动 → 未签到才签到 → 汇报积分
# 仅执行登录、签到状态查询与每日签到；不补签、不兑换积分。
#
# 作者 lcmovie  https://github.com/lcmovie/YYB-GO-Script-i

import json
import os
import random
import string
import sys
import time

import requests

APP_ID = "wx92782ef90ebc836d"
KDT_ID = "149536603"
PAGE_VERSION = "17"
CLIENT_VERSION = "2.226.7.101"
HOME_PATH = "pages/home/dashboard/index"
H5_URL = "https://h5.youzan.com"
AUTH_URL = "https://uic.youzan.com/passport/general/auth.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541f0d) XWEB/25715"
)
TIMEOUT = 30


def routes():
    values = []
    for lineno, raw in enumerate(os.getenv("YYB_SERVER", "").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        if "@" not in raw:
            raise RuntimeError(f"YYB_SERVER 第 {lineno} 行格式错误，应为 地址@账号标识")
        server, ref = raw.rsplit("@", 1)
        server, ref = server.strip().rstrip("/"), ref.strip()
        if not server or not ref:
            raise RuntimeError(f"YYB_SERVER 第 {lineno} 行格式错误，应为 地址@账号标识")
        if not server.startswith(("http://", "https://")):
            server = "http://" + server
        values.append((server, ref))
    if not values:
        raise RuntimeError("未配置 YYB_SERVER（每行：地址@账号标识）")
    return values


def yyb_code(server, ref):
    response = requests.post(
        f"{server}/wxapp/getCode", json={"ref": ref, "app_id": APP_ID}, timeout=TIMEOUT
    )
    response.raise_for_status()
    body = response.json()
    if int(body.get("code", -1)) != 0:
        raise RuntimeError(f"YYB 取 code 失败：{body.get('msg') or body.get('message') or body}")
    result = (body.get("data") or {}).get("result")
    code = result if isinstance(result, str) else (result or {}).get("code")
    if not code:
        raise RuntimeError("YYB 未返回 data.result.code")
    return str(code)


def message(body):
    return str(body.get("msg") or body.get("message") or body.get("error") or "未知响应")


class Youzan:
    """一次登录会话：微信 code 换取 access_token 后，所有业务接口都带 access_token 调用。"""

    def __init__(self):
        self.session = requests.Session()
        self.uuid = "".join(random.choices(string.ascii_letters + string.digits, k=14)) + str(
            int(time.time() * 1000)
        )
        self.token = ""
        self.uid = 0
        self.sid = ""
        self.nickname = ""

    def headers(self, extra, content_type="application/json"):
        return {
            "user-agent": UA,
            "content-type": content_type,
            "xweb_xhr": "1",
            "extra-data": json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
            "accept": "*/*",
            "sec-fetch-site": "cross-site",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "referer": f"https://servicewechat.com/{APP_ID}/{PAGE_VERSION}/page-frame.html",
            "accept-language": "zh-CN,zh;q=0.9",
        }

    def extra(self, is_weapp):
        # 小程序端固定上报的客户端指纹；sid 登录前为空，登录后回填会话号。
        data = {"version": CLIENT_VERSION, "client": "weapp", "uuid": self.uuid,
                "ftime": str(int(time.time() * 1000))}
        if is_weapp:
            data.update({"is_weapp": 1, "sid": self.sid, "bizEnv": "wsc"})
        else:
            data.update({"sid": "", "clientType": "weapp-miniprogram", "bizEnv": ""})
        return data

    def json(self, response, name):
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"{name} 返回非 JSON：HTTP {response.status_code}") from exc

    def login(self, code):
        payload = {
            "appId": APP_ID,
            "code": code,
            "platformName": "weapp",
            "signature": "windows",
            "clientId": "".join(random.choices("0123456789abcdef", k=18)),
            "grantType": "yz_union",
            "inWsc": True,
            "kdtId": KDT_ID,
            "extraBizData": {
                "enterOptions": {
                    "extKdtId": int(KDT_ID),
                    "path": HOME_PATH,
                    "query": {},
                    "scene": 1005,
                    "referrerInfo": {},
                },
                "guideBizDataMap": {"from_params": ""},
                "sceneData": {},
            },
        }
        headers = self.headers(self.extra(False))
        headers["app-mode"] = "default"
        headers["page-path"] = HOME_PATH
        response = self.session.post(
            AUTH_URL,
            params={"kdt_id": KDT_ID, "app_id": APP_ID},
            json=payload,
            headers=headers,
            timeout=TIMEOUT,
        )
        body = self.json(response, "auth.json")
        if int(body.get("code", -1)) != 0:
            raise RuntimeError(f"登录失败：{message(body)}")
        data = body.get("data") or {}
        self.token = str(data.get("accessToken") or "")
        self.uid = data.get("userId") or 0
        self.sid = str(data.get("sessionId") or "")
        self.nickname = str(data.get("nickname") or data.get("nickName") or "").strip()
        if not self.token or not self.uid:
            raise RuntimeError("登录响应缺少 accessToken/userId")

    def call(self, path, params=None, method="GET", data=None, content_type="application/json"):
        query = {"app_id": APP_ID, "kdt_id": KDT_ID, "access_token": self.token}
        query.update(params or {})
        response = self.session.request(
            method,
            f"{H5_URL}{path}",
            params=query,
            data=data,
            headers=self.headers(self.extra(True), content_type),
            timeout=TIMEOUT,
        )
        return self.json(response, path)

    def create_client(self):
        # 建会话，失败不影响后续带 access_token 的接口，仅记录。
        body = self.call(
            "/wscuser/weapp/create-client.json",
            method="POST",
            data={"appName": "wsc-weapp", "yzUid": str(self.uid)},
            content_type="application/x-www-form-urlencoded",
        )
        return int(body.get("code", -1)) == 0

    def checkin_id(self):
        body = self.call("/wscump/checkin/check-in-info.json")
        if int(body.get("code", -1)) != 0:
            return None, f"签到信息查询失败：{message(body)}"
        return (body.get("data") or {}).get("checkInId"), ""

    def activity(self, checkin_id):
        body = self.call("/wscump/checkin/get_activity_by_yzuid_v2.json",
                         {"checkinId": checkin_id})
        if int(body.get("code", -1)) != 0:
            return None, f"签到活动查询失败：{message(body)}"
        return body.get("data") or {}, ""

    def checkin(self, checkin_id):
        body = self.call("/wscump/checkin/checkinV2.json", {"checkinId": checkin_id})
        if int(body.get("code", -1)) != 0:
            return f"签到失败：{message(body)}"
        data = body.get("data") or {}
        if data.get("success") is False:
            return f"签到失败：{data.get('desc') or '服务端返回 success=false'}"
        awards = []
        for item in data.get("list") or []:
            if not isinstance(item, dict):
                continue
            infos = item.get("infos") or {}
            title = str(infos.get("title") or infos.get("desc") or "").strip()
            if title:
                awards.append(title)
        text = "签到成功"
        if awards:
            text += f"：{'、'.join(awards)}"
        times = data.get("times")
        if times is not None:
            text += f"（连续 {times} 天）"
        return text

    def points(self):
        body = self.call(
            "/wscuser/membercenter/stats.json",
            {"currentKdtId": KDT_ID, "version": CLIENT_VERSION, "needConsumptionAboveCoupon": 1},
        )
        if int(body.get("code", -1)) != 0:
            return None
        return ((body.get("data") or {}).get("stats") or {}).get("points")


def notify(lines):
    if os.getenv("YSJF_NOTIFY", "1").lower() in {"0", "false", "no"}:
        return
    for path in (os.path.dirname(os.path.abspath(__file__)), "/ql/data/scripts", "/ql/scripts"):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        from notify import send
        send("影视飓风签到", "\n".join(lines))
    except Exception as exc:
        print(f"[通知] 发送失败（不影响任务）：{exc}")


def run_one(index, server, ref):
    result = [f"账号 {index}（YYB {ref}）"]
    try:
        client = Youzan()
        client.login(yyb_code(server, ref))
        if client.nickname:
            result.append(f"用户：{client.nickname}")
        client.create_client()
        checkin_id, error = client.checkin_id()
        if error:
            result.append(error)
        elif not checkin_id:
            result.append("当前无签到活动")
        else:
            activity, error = client.activity(checkin_id)
            if error:
                result.append(error)
            elif not activity.get("isOpen", True):
                result.append("签到活动未开启")
            elif activity.get("isCheckin"):
                result.append(f"今日已签到（连续 {activity.get('continuesDay', '?')} 天）")
            else:
                result.append(client.checkin(checkin_id))
        points = client.points()
        if points is not None:
            result.append(f"当前积分：{points}")
    except Exception as exc:
        result.append(f"失败：{exc}")
    print(" | ".join(result))
    return result


def main():
    output = ["影视飓风：每日签到（服务端判定已签到则跳过）"]
    for index, (server, ref) in enumerate(routes(), 1):
        output.extend(run_one(index, server, ref))
    notify(output)


if __name__ == "__main__":
    main()
