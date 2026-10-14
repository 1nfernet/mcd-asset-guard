"""统一 CLI 入口。

用法（在项目根目录运行）：
  离线演示（无需 Token，回放 tests/fixtures 真实响应）：
    python -m src.main personal doctor
    python -m src.main personal points --as-of 2026-10-28
    python -m src.main personal mall --plan
    python -m src.main personal party
    python -m src.main personal party --watch --interval 60

  在线模式（设置环境变量 MCD_MCP_TOKEN，或复制 .env.example 为 .env）：
    python -m src.main personal points
    python -m src.main personal party --spu-id 6971 --city 350200 --store 1410056

架构铁律：取数交给 MCP、算术交给代码、表达交给 Agent（本 CLI 内零 LLM 调用）。
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import sys
import time

from src.config import Config
from src.errors import McdError
from src.mcp_client import McpClient
from src.models import AccountSnapshot
from src.notify import Notify, NotifyState
from src.report.render import banner, kv, supports_color
from src.services.personal.mall import MallService
from src.services.personal.mall import render_gaps, render_mall, render_plan
from src.services.personal.party import PartyService, render_party
from src.services.personal.points import PointsService, render_points
from src.state import JsonStore, TtlCache

# 离线演示缺省锚点（对应 tests/fixtures 里的真实响应）：属性名 -> (值, CLI 参数名)
OFFLINE_PARTY = {
    "spu_id": (6971, "--spu-id"),
    "city": ("350200", "--city"),
    "store": ("1410056", "--store"),
    "date": ("2026-10-17", "--date"),
}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--offline", action="store_true", help="强制使用 fixtures 演示数据")
    common.add_argument("--as-of", metavar="YYYY-MM-DD", help="模拟日期（演示临期分级）")
    common.add_argument("--json", action="store_true", help="以 JSON 输出（便于自动化）")
    common.add_argument("--verbose", action="store_true", help="控制台输出调试日志")

    p = argparse.ArgumentParser(
        prog="mcd-guard",
        description="麦麦资产管家：个人资产不蒸发 + 团队消费不虚花",
    )
    sub = p.add_subparsers(dest="group", required=True)

    per = sub.add_parser("personal", help="个人资产守护（v1.0）")
    psub = per.add_subparsers(dest="cmd", required=True)
    psub.add_parser("doctor", parents=[common], help="连接自检（now-time-info）")

    pt = psub.add_parser("points", parents=[common], help="积分看护：月末推算 + 7/3/1 分级")
    pt.add_argument("--watch", action="store_true", help="周期重查（默认一次性）")
    pt.add_argument("--interval", type=float, default=3600.0, metavar="SEC")

    ml = psub.add_parser("mall", parents=[common], help="商城监控 + 兑换规划")
    ml.add_argument("--cat", default="2>8", help="类目：2>8 周边 / 2>9 礼品卡 / 1>4 商品券，逗号分隔")
    ml.add_argument("--plan", action="store_true", help="输出兑换规划")
    ml.add_argument("--strategy", choices=["rescue", "value"], default="rescue")
    ml.add_argument("--all", action="store_true", help="显示全部状态（默认仅上架/预热）")

    pa = psub.add_parser("party", parents=[common], help="派对空位查询 / watch 轮询")
    pa.add_argument("--spu-id", type=int)
    pa.add_argument("--city", help="城市 code（query-party-city 返回）")
    pa.add_argument("--store", help="门店 storeCode（query-party-store 返回）")
    pa.add_argument("--date", help="日期 YYYY-MM-DD（query-party-store-date 返回）")
    pa.add_argument("--days", type=int, default=7, help="watch 向前看几天")
    pa.add_argument("--watch", action="store_true", help="常驻轮询，空位变化立即通知")
    pa.add_argument("--interval", type=float, default=60.0, metavar="SEC")

    sub.add_parser("squad", help="团队拼单（v1.1，Phase 2 实现）")
    return p


def setup_logging(cfg: Config, verbose: bool) -> None:
    fmt = "%(asctime)s %(levelname)-7s %(name)s %(message)s"
    run_log = cfg.log_dir / f"run-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
    file_h = logging.FileHandler(run_log, encoding="utf-8")
    file_h.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    logging.basicConfig(level=logging.DEBUG, format=fmt, handlers=[file_h, console])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("mcd.main")
    log.info("运行日志: %s", run_log)


def _notify(cfg) -> Notify:
    return Notify(
        supports_color(),
        NotifyState(JsonStore(cfg.state_dir / "notify-state.json")),
    )


# ---------------------------------------------------------------- 命令


def cmd_doctor(cfg, client, args) -> int:
    data = client.call("now-time-info") or {}
    if args.json:
        print(json.dumps({"mode": "offline" if cfg.offline else "online", "data": data},
                         ensure_ascii=False, indent=2, default=str))
        return 0
    lines = [banner("mcd-asset-guard doctor")]
    lines += kv(
        [
            ("模式", "offline（fixtures 回放）" if cfg.offline else "online"),
            ("端点", cfg.fixtures_dir if cfg.offline else cfg.endpoint),
            ("写操作", "锁定" if not cfg.enable_write_actions else "已放行（谨慎！）"),
            ("服务器时间", data.get("formatted", "-")),
            ("时区", data.get("timezone", "-")),
            ("信封解析", "OK（code=200）"),
            ("traceId", client.last_trace_id or "-"),
        ]
    )
    print("\n".join(lines))
    return 0


def cmd_points(cfg, client, args) -> int:
    svc = PointsService(client, _notify(cfg), as_of=cfg.as_of)

    def run_once() -> None:
        rep = svc.evaluate()
        if args.json:
            print(json.dumps(dataclasses.asdict(rep), ensure_ascii=False,
                             indent=2, default=str))
        else:
            print(render_points(rep))

    run_once()
    if args.watch:
        print(f"\n[watch] 每 {args.interval:.0f}s 重查一次，Ctrl-C 退出")
        while True:
            time.sleep(args.interval)
            run_once()
    return 0


def cmd_mall(cfg, client, args) -> int:
    svc = MallService(client, as_of=cfg.as_of)
    prods = svc.products(args.cat)
    now = svc.now()
    shown = prods if args.all else [p for p in prods if p.is_on_sale or p.is_presale]
    print(render_mall(shown, now))

    if args.plan:
        snap = AccountSnapshot.from_payload(client.call("query-my-account") or {})
        expiring = snap.current_month_expire
        plan = svc.plan(
            [p for p in prods if p.is_on_sale], snap.available, expiring,
            strategy=args.strategy, now=now,
        )
        print()
        print(render_plan(plan, expiring))
        gaps = svc.gaps(prods, snap.available)
        g = render_gaps(gaps)
        if g:
            print()
            print(g)
    return 0


def cmd_party(cfg, client, args) -> int:
    svc = PartyService(
        client,
        TtlCache(JsonStore(cfg.state_dir / "party-cache.json")),
        JsonStore(cfg.state_dir / "party-snapshot.json"),
    )

    # 离线演示时补缺省锚点（在线模式要求显式给出或交互列出）
    if cfg.offline:
        for attr, (val, flag) in OFFLINE_PARTY.items():
            if getattr(args, attr) is None:
                setattr(args, attr, val)
                print(f"[offline] 演示缺省 {flag} {val}", file=sys.stderr)

    products = svc.party_products()
    if not args.spu_id:
        print("请先选择派对商品（--spu-id）：")
        for p in products:
            print(f"  spu {p.spu_id}  {p.name}  {p.point} 积分")
        return 2
    product = next((p for p in products if p.spu_id == args.spu_id), None)
    product_name = product.name if product else f"spu-{args.spu_id}"

    if not args.city:
        print("城市列表（--city 选一个）：")
        for c in svc.cities(args.spu_id):
            print(f"  {c.code}  {c.name}")
        return 2
    city = next(
        (c for c in svc.cities(args.spu_id) if str(c.code) == str(args.city)), None
    )
    if city is None:
        print(f"--city {args.city} 不在该 spu 的城市列表里", file=sys.stderr)
        return 2

    if not args.store:
        print("门店列表（--store 选一个）：")
        for s in svc.stores(args.spu_id, city):
            print(f"  {s.code}  {s.name}  {s.address}")
        return 2

    if args.watch:
        if cfg.offline:
            # 离线 fixtures 只覆盖演示日期的场次样本，不编造数据
            dates = [OFFLINE_PARTY["date"][0]]
            print(
                f"[offline] watch 仅轮询 fixtures 覆盖的演示日期 {dates[0]}；"
                "在线模式按 --days 展开未来 N 天",
                file=sys.stderr,
            )
        else:
            dates = svc.dates(args.spu_id, args.store)[: args.days]
        notify = Notify(supports_color(), None)
        print(
            f"[watch] spu={args.spu_id} store={args.store} 未来 {len(dates)} 天，"
            f"间隔 {args.interval:.0f}s，Ctrl-C 退出"
        )
        svc.watch(
            args.spu_id, args.store, dates, interval=args.interval,
            emit=lambda e: notify.emit(
                "ok" if e.kind == "increased" else "info",
                f"派对空位 {e.kind}",
                [
                    f"{e.date} {e.session.time_start}-{e.session.time_end} "
                    f"剩余 {e.session.left_num} · {e.detail}"
                ],
            ),
        )
        return 0

    if not args.date:
        dates = svc.dates(args.spu_id, args.store)
        print(f"可预约日期（--date 选一个，前 10 个）：")
        for d in dates[:10]:
            print(f"  {d}")
        return 2

    store = next(
        (s for s in svc.stores(args.spu_id, city) if s.code == args.store), None
    )
    store = store or type("StoreView", (), {"name": args.store, "code": args.store})()
    sessions = svc.sessions(args.spu_id, args.store, args.date)
    print(render_party(product_name, args.spu_id, city.name, store, args.date, sessions))
    return 0


def cmd_squad(cfg, client, args) -> int:
    print(
        "squad（团队拼单）为 v1.1 模块，将在 Phase 2 实现。\n"
        "架构已预留：券组合穷举 / 分账到分 / CLI 需求收集；\n"
        "启用开关：SQUAD_ENABLED=true（见 .env.example）。"
    )
    return 2


# ---------------------------------------------------------------- 入口


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(offline=args.offline, as_of=args.as_of)
    if cfg.auto_offline:
        print("[offline] 未检测到 MCD_MCP_TOKEN，自动进入 fixtures 演示模式",
              file=sys.stderr)
    setup_logging(cfg, args.verbose)
    client = McpClient.create(cfg)

    handlers = {"doctor": cmd_doctor, "points": cmd_points,
                "mall": cmd_mall, "party": cmd_party}
    try:
        if args.group == "squad":
            return cmd_squad(cfg, client, args)
        return handlers[args.cmd](cfg, client, args)
    except McdError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n退出")
        return 130


if __name__ == "__main__":
    sys.exit(main())
