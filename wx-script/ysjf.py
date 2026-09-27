"""
name: 影视飓风Club
入口: 有赞微信小程序（默认店铺：影视飓风Club）
功能: 每日签到领积分，并输出连签天数与本月签到日历
变量: YYB_SERVER    (YYB-Go-Enhanced 地址@微信账号标识，多账号换行分隔)
      YZ_APPID      (可选) 有赞小程序 appId，默认 wx92782ef90ebc836d
      YZ_KDT_ID     (可选) 有赞店铺 kdtId，默认 149536603
      YZ_CLIENT_ID  (可选) 客户端标识，默认随机生成
      YZ_SIGNATURE  (可选) 终端标识，默认 windows，可选 android/ios
定时: 每天一次
cron: 20 8 * * *
依赖: requests
说明: 换其它有赞小程序时，只需按抓包结果改 YZ_APPID 与 YZ_KDT_ID
作者：lcmovie https://github.com/lcmovie
------------更新日志------------
2026/9/20 V1.0 基于微信小程序抓包初始化，接入 YYB-Go-Enhanced 取码登录，实现每日签到
"""

import json
import os
import random
import string
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, Optional, Tuple

import requests


DEFAULT_APP_ID = "wx92782ef90ebc836d"
DEFAULT_KDT_ID = "149536603"

VERSION = "2.226.7.101"
PAGE_PATH = "pages/home/dashboard/index"
APP_MODE = "default"
WEAPP_CODE_VERSION = "17"
ENTER_SCENE = 1256

AUTH_URL = "https://uic.youzan.com/passport/general/auth.json"
CREATE_CLIENT_URL = "https://h5.youzan.com/wscuser/weapp/create-client.json"
CHECKIN_INFO_URL = "https://h5.youzan.com/wscump/checkin/check-in-info.json"
ACTIVITY_URL = "https://h5.youzan.com/wscump/checkin/get_activity_by_yzuid_v2.json"
CHECKIN_URL = "https://h5.youzan.com/wscump/checkin/checkinV2.json"
MONTH_URL = "https://h5.youzan.com/wscump/checkin/find_checkin_info_by_month.json"

TZ = timezone(timedelta(hours=8))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) "
    "NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090c37)XWEB/14315"
)


class YouzanTask:
    def __init__(self) -> None:
        self.logs = []
        self.success_count = 0
        self.failed_count = 0
        self.app_id = (os.getenv("YZ_APPID") or DEFAULT_APP_ID).strip()
        self.kdt_id = (os.getenv("YZ_KDT_ID") or DEFAULT_KDT_ID).strip()
        self.client_id = (os.getenv("YZ_CLIENT_ID") or uuid.uuid4().hex[:18]).strip()
        self.signature = (os.getenv("YZ_SIGNATURE") or "windows").strip()
        alphabet = string.ascii_letters + string.digits
        self.device_uuid = (
            "V7" + "".join(random.choices(alphabet, k=13)) + str(int(time.time() * 1000))
        )
        self.access_token = ""
        self.session_id = ""
        self.yz_uid: Optional[int] = None

    def log(self, message: str) -> None:
        print(message)
        self.logs.append(message)

    def accounts(self) -> Iterator[Tuple[str, str]]:
        raw_value = os.getenv("YYB_SERVER", "")
        if not raw_value.strip():
            self.log("❌ 未配置 YYB_SERVER，格式：地址@微信账号标识（多账号换行）")
            return
        for line_no, raw in enumerate(raw_value.splitlines(), 1):
            value = raw.strip()
            if not value:
                continue
            if "@" not in value:
                self.log(f"❌ YYB_SERVER 第 {line_no} 行格式错误，已跳过")
                continue
            server, ref = value.rsplit("@", 1)
            if not server.strip() or not ref.strip():
                self.log(f"❌ YYB_SERVER 第 {line_no} 行地址或账号标识为空，已跳过")
                continue
            yield server.strip(), ref.strip()

    def get_wx_code(self, server: str, ref: str) -> Optional[str]:
        host = server.rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        try:
            response = requests.post(
                f"{host}/wxapp/getCode",
                json={"ref": ref, "app_id": self.app_id},
                timeout=20,
            )
            response.raise_for_status()
            body = response.json()
            code = ((body.get("data") or {}).get("result") or {}).get("code")
            if body.get("code") != 0 or not code:
                self.log(f"❌ 获取微信 code 失败，YYB-Go 响应码：{body.get('code')}")
                return None
            self.log("✅ 获取微信 code 成功")
            return code
        except Exception as exc:
            self.log(f"❌ 获取微信 code 异常：{exc}")
            return None

    def new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Content-Type": "application/json",
                "xweb_xhr": "1",
                "page-path": PAGE_PATH,
                "app-mode": APP_MODE,
                "Referer": (
                    f"https://servicewechat.com/{self.app_id}/{WEAPP_CODE_VERSION}/page-frame.html"
                ),
            }
        )
        return session

    def extra_data(self, is_weapp: bool) -> str:
        ftime = int(time.time() * 1000)
        if is_weapp:
            payload: Dict[str, Any] = {
                "is_weapp": 1,
                "sid": self.session_id,
                "version": VERSION,
                "client": "weapp",
                "bizEnv": "wsc",
                "uuid": self.device_uuid,
                "ftime": ftime,
            }
        else:
            payload = {
                "sid": "",
                "version": VERSION,
                "clientType": "weapp-miniprogram",
                "client": "weapp",
                "bizEnv": "",
                "uuid": self.device_uuid,
                "ftime": ftime,
            }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def api_headers(self) -> Dict[str, str]:
        return {"extra-data": self.extra_data(True)}

    def api_params(self) -> Dict[str, str]:
        return {
            "app_id": self.app_id,
            "kdt_id": self.kdt_id,
            "access_token": self.access_token,
        }

    def login(self, session: requests.Session, code: str) -> bool:
        try:
            kdt_id_value: Any = int(self.kdt_id)
        except ValueError:
            kdt_id_value = self.kdt_id

        payload = {
            "appId": self.app_id,
            "code": code,
            "platformName": "weapp",
            "signature": self.signature,
            "clientId": self.client_id,
            "grantType": "yz_union",
            "inWsc": True,
            "kdtId": self.kdt_id,
            "extraBizData": {
                "enterOptions": {
                    "extKdtId": kdt_id_value,
                    "path": PAGE_PATH,
                    "query": {},
                    "scene": ENTER_SCENE,
                    "referrerInfo": {},
                    "apiCategory": "default",
                },
                "guideBizDataMap": {"from_params": ""},
                "sceneData": {},
            },
        }
        params = {"kdt_id": self.kdt_id, "app_id": self.app_id}
        headers = {"extra-data": self.extra_data(False)}
        try:
            response = session.post(
                AUTH_URL, params=params, json=payload, headers=headers, timeout=20
            )
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or {}
            if body.get("code") != 0 or not data.get("accessToken"):
                self.log(f"❌ 有赞登录失败：code={body.get('code')}，msg={body.get('msg')}")
                return False
            self.access_token = data.get("accessToken")
            self.session_id = data.get("sessionId") or ""
            self.yz_uid = data.get("userId")
            self.log(f"✅ 有赞登录成功：{data.get('nickname') or '未知昵称'}（yzUid={self.yz_uid}）")
            return True
        except Exception as exc:
            self.log(f"❌ 有赞登录异常：{exc}")
            return False

    def create_client(self, session: requests.Session) -> None:
        try:
            response = session.post(
                CREATE_CLIENT_URL,
                params=self.api_params(),
                json={"appName": "wsc-weapp", "yzUid": self.yz_uid},
                headers=self.api_headers(),
                timeout=20,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                self.log(f"⚠️ 创建客户端失败：{body.get('msg')}")
        except Exception as exc:
            self.log(f"⚠️ 创建客户端异常：{exc}")

    def get_json(
        self, session: requests.Session, url: str, params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            response = session.get(
                url, params=params, headers=self.api_headers(), timeout=20
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            self.log(f"❌ 请求异常：{url} -> {exc}")
            return None

    def checkin_info(self, session: requests.Session) -> Optional[Dict[str, Any]]:
        body = self.get_json(session, CHECKIN_INFO_URL, self.api_params())
        if not body:
            return None
        if body.get("code") != 0:
            self.log(f"❌ 查询签到活动失败：{body.get('msg')}")
            return None
        data = body.get("data") or {}
        if not data.get("checkInId"):
            self.log("❌ 该店铺未配置签到活动")
            return None
        return data

    def activity(
        self, session: requests.Session, checkin_id: Any
    ) -> Optional[Dict[str, Any]]:
        params = dict(self.api_params())
        params["checkinId"] = str(checkin_id)
        body = self.get_json(session, ACTIVITY_URL, params)
        if not body:
            return None
        if body.get("code") != 0:
            self.log(f"❌ 查询签到状态失败：{body.get('msg')}")
            return None
        return body.get("data") or {}

    def checkin(self, session: requests.Session, checkin_id: Any) -> Tuple[bool, str]:
        params = dict(self.api_params())
        params["checkinId"] = str(checkin_id)
        body = self.get_json(session, CHECKIN_URL, params)
        if not body:
            return False, "签到请求异常"
        data = body.get("data") or {}
        if body.get("code") == 0 and data.get("success") is True:
            rewards = []
            for item in data.get("list") or []:
                title = ((item.get("infos") or {}).get("title") or "").strip()
                if title:
                    rewards.append(title)
            return True, f"{data.get('desc') or '签到成功'}：{'、'.join(rewards) or '未知奖励'}"
        return False, f"签到失败：code={body.get('code')}，msg={body.get('msg')}"

    def month_days(self, session: requests.Session, checkin_id: Any) -> int:
        now = datetime.now(TZ)
        params = dict(self.api_params())
        params.update(
            {
                "checkin_id": str(checkin_id),
                "year": str(now.year),
                "month": str(now.month),
            }
        )
        body = self.get_json(session, MONTH_URL, params)
        if not body:
            return 0
        return len((body.get("data") or {}).get("checkin_date") or [])

    @staticmethod
    def daily_reward_text(activity_data: Dict[str, Any]) -> str:
        rewards = activity_data.get("dailyRewards") or []
        return "、".join((item.get("desc") or "").strip() for item in rewards if item.get("desc"))

    @staticmethod
    def next_reward_text(activity_data: Dict[str, Any]) -> str:
        days = activity_data.get("continuesDay") or 0
        pending = []
        for item in activity_data.get("rewards") or []:
            if item.get("fetched"):
                continue
            try:
                duration = int(item.get("duration"))
            except (TypeError, ValueError):
                continue
            prize_list = item.get("prize") or [{}]
            points = ((prize_list[0].get("desc") or {}) if prize_list else {}).get("middle")
            pending.append((duration, points))
        for duration, points in sorted(pending):
            if duration > days:
                reward = f"{points} 积分" if points else "奖励"
                return f"距 {duration} 天连签奖励（{reward}）还差 {duration - days} 天"
        return ""

    def sign_in(self, session: requests.Session) -> bool:
        info = self.checkin_info(session)
        if not info:
            return False
        checkin_id = info.get("checkInId")
        self.log(f"签到活动：checkInId={checkin_id}")

        activity = self.activity(session, checkin_id)
        if not activity:
            return False
        if activity.get("isOpen") is False:
            self.log("❌ 签到活动未开启")
            return False

        daily_reward = self.daily_reward_text(activity)
        if daily_reward:
            self.log(f"每日奖励：{daily_reward}")

        if activity.get("isCheckin") is True:
            self.log(f"✅ 今日已签到，连续 {activity.get('continuesDay') or 0} 天")
            return True

        time.sleep(random.uniform(1, 2))
        ok, message = self.checkin(session, checkin_id)
        self.log(("✅ " if ok else "❌ ") + message)
        if not ok:
            return False

        time.sleep(random.uniform(1, 2))
        latest = self.activity(session, checkin_id) or {}
        if latest.get("isCheckin") is True:
            self.log(f"✅ 连续签到 {latest.get('continuesDay') or 0} 天")
        next_reward = self.next_reward_text(latest)
        if next_reward:
            self.log(f"✅ {next_reward}")
        days = self.month_days(session, checkin_id)
        if days:
            self.log(f"✅ 本月已签到 {days} 天")
        return True

    def notify(self) -> None:
        title = "有赞小程序签到"
        content = "\n".join(self.logs)
        try:
            from notify import send

            send(title, content)
            self.log("✅ 青龙通知调用完成")
        except Exception as exc:
            self.log(f"⚠️ 青龙通知未发送：{exc}")

    def run(self) -> int:
        self.log("===== 有赞小程序签到开始 =====")
        self.log(f"小程序 appId：{self.app_id}，店铺 kdtId：{self.kdt_id}")
        account_list = list(self.accounts())
        if not account_list:
            self.failed_count = 1
        for index, (server, ref) in enumerate(account_list, 1):
            self.log(f"\n----- 账号 {index} -----")
            self.access_token = ""
            self.session_id = ""
            self.yz_uid = None
            session = self.new_session()
            code = self.get_wx_code(server, ref)
            ok = bool(code) and self.login(session, code)
            if ok:
                self.create_client(session)
                time.sleep(random.uniform(1, 2))
                ok = self.sign_in(session)
            if ok:
                self.success_count += 1
            else:
                self.failed_count += 1
            time.sleep(random.uniform(1, 2))

        self.log(f"\n===== 执行完成：成功 {self.success_count}，失败 {self.failed_count} =====")
        self.notify()
        return 0 if self.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(YouzanTask().run())
