from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

REQUIRED = [
    ("AzureWebJobsStorage", "Function App 필수. Timer 상태·Event Hub 체크포인트 저장", True),
    ("EventHubConnection", "Event Hub 연결 문자열 (바인딩이 이 '이름'을 찾는다)", True),
    ("BED_EVENT_HUB_NAME", "%BED_EVENT_HUB_NAME% 로 참조됨. 없으면 함수 등록 실패", True),
    ("BED_EVENT_HUB_CONSUMER_GROUP", "%...% 로 참조됨. 기본값은 $Default", True),
    ("DATA_GO_KR_SERVICE_KEY", "공공데이터 디코딩 키 (응급의료 + 기상청 공용)", True),
    ("PGHOST", "PostgreSQL 호스트", True),
    ("PGDATABASE", "데이터베이스 이름", True),
    ("PGUSER", "사용자", True),
    ("PGPASSWORD", "비밀번호", True),
    ("PGSSLMODE", "Azure PostgreSQL은 require 여야 한다", False),
    ("PGPORT", "기본 5432", False),
    ("BED_COLLECT_DISTRICTS", "수집 범위. 기본 서울특별시", False),
    ("TMAP_APP_KEY", "FastAPI 전용. Function App에는 없어도 된다", False),
]

AZURE_ONLY = [
    ("FUNCTIONS_EXTENSION_VERSION", "~4"),
    ("AzureWebJobsFeatureFlags", "EnableWorkerIndexing"),
]


def load_local_settings() -> dict[str, str]:
    path = BACKEND_DIR / "local.settings.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")).get("Values", {})
    except (ValueError, OSError) as exc:
        print(f"local.settings.json 읽기 실패: {exc}", file=sys.stderr)
        return {}


def check(values: dict[str, str], source: str) -> int:
    print(f"[{source}] 설정 점검\n")
    missing_critical: list[str] = []

    for name, note, critical in REQUIRED:
        value = values.get(name, "")
        if value:
            shown = "***" if any(k in name for k in ("KEY", "PASSWORD", "Connection", "Storage")) else value
            print(f"  OK    {name:<30} {shown}")
        else:
            mark = "빠짐 " if critical else "선택 "
            print(f"  {mark} {name:<30} {note}")
            if critical:
                missing_critical.append(name)

    print()
    if missing_critical:
        print(f"필수 설정 {len(missing_critical)}개 누락: {', '.join(missing_critical)}")
        print("→ 이 상태로 배포하면 함수가 등록되지 않거나 실행 시 실패한다.\n")
    else:
        print("필수 설정이 모두 있다.\n")

    print("Azure Function App에만 필요한 설정(로컬에는 없음):")
    for name, expected in AZURE_ONLY:
        print(f"  - {name} = {expected}")
    print()
    return 1 if missing_critical else 0


def print_az_command(values: dict[str, str]) -> None:
    print("# 값을 채운 뒤 실행하세요 (<APP>, <RG> 교체)\n")
    print("az functionapp config appsettings set \\")
    print("  --name <APP> --resource-group <RG> \\")
    print("  --settings \\")

    pairs = [
        ("FUNCTIONS_WORKER_RUNTIME", "python"),
        ("FUNCTIONS_EXTENSION_VERSION", "~4"),
        ("AzureWebJobsFeatureFlags", "EnableWorkerIndexing"),
    ]
    for name, _note, _critical in REQUIRED:
        if name in ("FUNCTIONS_WORKER_RUNTIME", "AzureWebJobsStorage"):
            continue
        if name == "TMAP_APP_KEY":  # FastAPI 전용
            continue
        pairs.append((name, values.get(name) or f"<{name}>"))

    for index, (name, value) in enumerate(pairs):
        tail = " \\" if index < len(pairs) - 1 else ""
        print(f'    {name}="{value}"{tail}')
    print("\n# PGSSLMODE 는 Azure PostgreSQL 이면 반드시 require")


def check_imports() -> int:
    sys.path.insert(0, str(BACKEND_DIR))
    try:
        import function_app
    except Exception as exc: 
        print(f"인덱싱 실패: {type(exc).__name__}: {exc}")
        print("→ requirements.txt 설치 누락이거나 import 오류. 배포해도 함수가 안 보인다.")
        return 1

    names = [f.get_function_name() for f in function_app.app.get_functions()]
    print(f"인덱싱 성공: {len(names)}개 - {', '.join(names)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Azure Functions 배포 전 점검")
    parser.add_argument("--print-az-command", action="store_true", help="앱 설정 등록 명령 출력")
    parser.add_argument("--check-imports", action="store_true", help="인덱싱 가능 여부 확인")
    parser.add_argument("--from-env", action="store_true", help="local.settings.json 대신 현재 환경변수")
    args = parser.parse_args(argv)

    values = dict(os.environ) if args.from_env else load_local_settings()
    source = "환경변수" if args.from_env else "local.settings.json"

    if args.print_az_command:
        print_az_command(values)
        return 0

    exit_code = check(values, source)
    if args.check_imports:
        print("-" * 60)
        exit_code = check_imports() or exit_code
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
