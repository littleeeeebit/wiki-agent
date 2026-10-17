"""Keep whole-suite execution under the repository's one final-verification owner."""

import pytest

from tool.verify import local_full_run


def pytest_configure(config):
    if local_full_run(config):
        raise pytest.UsageError(
            "전체 검사는 최종 검증 담당이 한 번만 실행합니다. 로컬 개발 중에는 변경한 테스트 파일이나 -k 선택만 실행하세요. "
            "최종 검증: python tool/verify.py final --base main (CI가 없으면 제한된 로컬 실행)"
        )
