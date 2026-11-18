"""配置域（原 foundation 顶层 3 文件，2026-11-18 归簇）。

文件布局：
    settings.py       原 ``foundation/settings.py`` —— 进程级配置唯一声明表
                      （ENV_TABLE / get_* / 旧名诊断，R-002 唯一 env 入口）
    paths.py          原 ``foundation/paths.py`` —— 数据路径唯一来源
                      （PROFILES / data_dir / session_db_filename，R-005）
    config_loader.py  原 ``foundation/config_loader.py`` —— 通用四层配置加载器

簇内依赖：``paths`` 惰性依赖 ``settings``（避免导入期成环）；其余互不依赖。

**本包不做 re-export**（R-014 / agent-dir-structuring-plan §3.3）：
所有引用必须指向具体子模块（``agentnodes.foundation.config.settings`` 等）。
"""
