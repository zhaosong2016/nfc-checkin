"""
NFC 卡片注册工具（支持 bus 列）
python3 register_cards.py
"""
import csv, time, sys
from pathlib import Path

ROSTER_FILE = "roster.csv"

def load_roster():
    path = Path(ROSTER_FILE)
    if not path.exists():
        print(f"错误：找不到名单文件 {ROSTER_FILE}")
        sys.exit(1)
    roster = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            roster.append({
                "name": row.get("name", ""),
                "uid": row.get("uid", "").strip(),
                "phone": row.get("phone", ""),
                "bus": row.get("bus", "1"),
            })
    return roster

def save_roster(roster):
    with open(ROSTER_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "uid", "phone", "bus"])
        for p in roster:
            writer.writerow([p["name"], p["uid"], p["phone"], p.get("bus", "1")])

def init_reader():
    try:
        from smartcard.System import readers
        reader_list = readers()
        if not reader_list:
            print("未检测到 NFC 读卡器！请插入 ACR122U。")
            sys.exit(1)
        reader = reader_list[0]
        print(f"已连接读卡器: {reader}")
        return reader
    except ImportError:
        print("未安装 pyscard 库！")
        print("请运行: pip install pyscard")
        sys.exit(1)

def read_uid(reader):
    from smartcard.util import toHexString
    try:
        connection = reader.createConnection()
        connection.connect()
        data, sw1, sw2 = connection.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
        if sw1 == 0x90 and sw2 == 0x00:
            return toHexString(data).replace(" ", ":")
    except:
        pass
    return None

def main():
    print("=" * 50)
    print("  NFC 卡片注册工具")
    print("=" * 50)
    print()

    roster = load_roster()
    reader = init_reader()

    bound = sum(1 for p in roster if p["uid"])
    total = len(roster)
    print(f"名单共 {total} 人，已绑定 {bound} 人")
    print()
    print("操作说明：")
    print("  - 将卡片放到读卡器上自动绑定")
    print("  - 按回车跳过当前人员")
    print("  - 输入 q 退出并保存")
    print("  - 输入 r 重新绑定上一个人")
    print("-" * 50)

    i = 0
    while i < total and roster[i]["uid"]:
        i += 1

    while i < total:
        person = roster[i]
        if person["uid"]:
            print(f"  [{i+1}/{total}] {person['name']} - 已绑定: {person['uid']}")
            i += 1
            continue

        bus_info = f" ({person.get('bus','1')}车)" if person.get('bus') else ""
        print(f"\n  [{i+1}/{total}] {person['name']}{bus_info}")
        print(f"  请将卡片放到读卡器上... (回车跳过, q退出, r返回)")

        last_uid = None
        while True:
            import select
            if select.select([sys.stdin], [], [], 0)[0]:
                cmd = input().strip().lower()
                if cmd == "q":
                    save_roster(roster)
                    bound = sum(1 for p in roster if p["uid"])
                    print(f"\n已保存！共绑定 {bound}/{total} 人")
                    return
                elif cmd == "r" and i > 0:
                    i -= 1
                    roster[i]["uid"] = ""
                    print(f"  已清除 {roster[i]['name']} 的绑定")
                    break
                elif cmd == "":
                    print(f"  跳过 {person['name']}")
                    i += 1
                    break

            uid = read_uid(reader)
            if uid and uid != last_uid:
                existing = [p for p in roster if p["uid"] == uid and p["name"] != person["name"]]
                if existing:
                    print(f"  ⚠ 此卡已绑定给 {existing[0]['name']}！请换一张卡")
                    last_uid = uid
                    continue

                person["uid"] = uid
                print(f"  ✓ {person['name']} 绑定成功: {uid}")
                last_uid = uid
                save_roster(roster)
                i += 1
                time.sleep(1)
                break

            time.sleep(0.3)

    save_roster(roster)
    bound = sum(1 for p in roster if p["uid"])
    print(f"\n全部完成！共绑定 {bound}/{total} 人")

if __name__ == "__main__":
    main()
