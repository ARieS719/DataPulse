"""S5：订单流模拟器 —— 向 Kafka 持续发送订单事件（JSON），供实时水位线消费。

用法：
  docker compose --profile realtime up -d                    # Kafka（KRaft）+ MySQL
  python scripts/stream_simulator.py --topic datapulse.orders --rate 20
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime


def main() -> int:
    try:
        from confluent_kafka import Producer
    except ImportError:
        print("缺少 confluent-kafka：pip install confluent-kafka", file=sys.stderr)
        return 2

    ap = argparse.ArgumentParser(description="DataPulse 订单流模拟器")
    ap.add_argument("--topic", default="datapulse.orders")
    ap.add_argument("--rate", type=float, default=20.0, help="每秒事件数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--bootstrap", default="localhost:9092")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    p = Producer({"bootstrap.servers": args.bootstrap})
    channels = ["抖音", "快手", "小红书", "微信小程序", "淘宝", "京东", "官网"]
    weights = [24, 12, 16, 14, 18, 11, 5]
    i = 0
    print(f"开始发送 → {args.topic}（{args.rate}/s，Ctrl+C 停止）")
    try:
        while True:
            i += 1
            event = {
                "order_id": f"RT-{int(time.time() * 1000):013d}-{i:06d}",
                "user_id": f"U{rng.randint(1, 100000):07d}",
                "order_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "channel": rng.choices(channels, weights=weights, k=1)[0],
                "amount": round(rng.uniform(9.9, 199.9), 2),
            }
            p.produce(args.topic, json.dumps(event, ensure_ascii=False).encode("utf-8"))
            p.poll(0)
            time.sleep(1.0 / args.rate)
    except KeyboardInterrupt:
        p.flush()
        print(f"\n已停止（共发送 {i} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
