# 安装与使用

本包是Codex技能，包含方法参考、OCR脚本、评分数据格式和Word导出脚本，不是一次阅卷结果。

解压后，将整个 `criminal-case-grader` 文件夹放入用户的 `$CODEX_HOME/skills/`；未自定义CODEX_HOME时，放入 `~/.codex/skills/`。不要只复制SKILL.md。当前电脑已安装在该位置。

上传题目、参考答案、考生答案后，在Codex中输入：

> 请用 $criminal-case-grader 批改这些刑法案例，每道完整案例20分，输出带红绿蓝标注的两栏Word报告，并附简明考场答案。

Word导出依赖Python 3.9+及python-docx；优先使用Codex配套运行时。完整说明见 `references/report-data.md`。

百度OCR读取环境变量 `AISTUDIO_ACCESS_TOKEN`，安装包不含凭证。缺令牌或云端服务不可用时按技能流程转本地OCR/视读；macOS本地识别脚本需要可用的Swift工具链。详见 `references/ocr.md`。本地OCR已用合成图片与PDF验证，云端接口只做文档核对与离线测试，未实测个人令牌。

可从技能目录运行 `python3 scripts/self_test.py` 检查分值、原文、字色、跨问证据和OCR状态处理。`assets/report-example.json` 只用于格式演示，不是真实法律案例或标准答案。
