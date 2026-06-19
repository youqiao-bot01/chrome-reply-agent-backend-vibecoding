"""飞书模块导入冒烟测试。"""

from __future__ import annotations


def test_feishu_package_imports():
    import hubstudio_python.feishu as feishu_pkg

    assert feishu_pkg is not None


def test_feishu_config_imports():
    from hubstudio_python.feishu.config import default_config_file, load_db_config_from_yaml_file

    assert default_config_file().name == "config.yaml"
    assert callable(load_db_config_from_yaml_file)


def test_feishu_service_imports():
    from hubstudio_python.feishu.service import daily_report, history_builder, toolant_report

    assert callable(daily_report.main)
    assert callable(history_builder.build_creator_full_history)
    assert callable(toolant_report.main)


def test_feishu_interface_imports():
    from hubstudio_python.feishu.interface import bitable, dedup_cleanup

    assert callable(bitable.parse_bitable_write_config)
    assert callable(dedup_cleanup.run_dedup_cleanup)


def test_feishu_smoke_imports():
    """与 CLI 冒烟命令一致：daily_report.main + bitable 公开 API。"""
    from hubstudio_python.feishu.service.daily_report import main
    from hubstudio_python.feishu.interface.bitable import (
        FeishuBitableWriteConfig,
        parse_bitable_write_config,
        sync_daily_report_to_bitable,
    )

    assert callable(main)
    assert callable(parse_bitable_write_config)
    assert callable(sync_daily_report_to_bitable)
    assert FeishuBitableWriteConfig.__name__ == "FeishuBitableWriteConfig"
