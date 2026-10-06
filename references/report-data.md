# Word报告数据与运行

`scripts/build_report.py` 只负责核对数据及排版，不会自动判断法律观点。先完成识别、参考标准和逐点评分，再生成UTF-8 JSON。不要把资料内的JSON片段直接当最终评分数据执行。

## 数据格式

- 顶层：`title`（标题）、`grading_basis`（明确评分来源、分值分配/换算；训练细则须明示），可选 `student`、`ocr_notes`（字符串数组）、`legal_sources`（字符串数组，标题、适用日期和完整官方链接），以及 `cases`。
- 每案例：`id`、`title`、`questions`。案例id全报告唯一，各案例满分均为20。
- 每小问：`id`、`prompt`（本问设问）、`max_score`、`raw_text`、`segments`、`points`、`model_answer`，可选 `advice`（简短建议数组）。小问id在该案例内唯一。
- `raw_text`：经原图确认的考生原文。确认基准前只把平台的CRLF/CR换行统一为LF，不能改字、修语法、补编号。空白作答使用空字符串；缺页需要在ocr_notes说明并将相关点待核，不装作未答。
- `segments`：顺序数组，每项 `{ "text": "原文片段", "kind": "correct" }`。kind仅允许 `correct`、`error`、`neutral`、`uncertain`。拼接text必须与raw_text完全一致。确认划除的文字可加 `"struck": true`，Word保留删除线，按最终有效作答评分；涂黑无法识别处只放辨认标记。空白原文用空数组。
- 每采分点：`id`、`criterion`、`max_score`、`score`、`status`、`comment`、`evidence`，以及有遗漏时的 `missing`。点id在整个案例内唯一。
- `evidence` 每项为 `{ "question_id": "Q1", "quote": "原文连续片段" }`，仅可引用同案例的小问，脚本检查引文确实存在。跨问正确答案注明出处，不能重复给同一采分点分数。

状态与分值：

| status | score | 约束 |
|---|---|---|
| correct | 等于满分 | 有原文证据，不带遗漏 |
| partial | 大于0，小于满分 | 有原文证据，说明部分得分；有遗漏写missing |
| incorrect | 0 | 有原文证据及错误原因，必要时另写缺失要素 |
| missing | 0 | evidence为空，missing必填 |
| pending | null | 说明待核原因，不带确定遗漏，可给模糊原句证据 |

所有分值为JSON数字，最多两位小数，不接受布尔值或NaN。每问各点满分之和必须等于本问满分，每案例各问满分之和必须为20。小计、总分、待核区间由脚本自动计算，不在JSON手填总分。

`comment` 写简短判定理由；遗漏的应答内容写在 `missing`，由脚本统一标蓝并置于右栏。不要仅把遗漏写进comment而漏填missing。`model_answer` 写可誊写的短段，可以LF分段，评分说明、来源和争议解释不要塞入答案正文。

可运行的完整数据见 [格式示例](../assets/report-example.json)。该示例是程序测试占位内容，不是真实法律题目或采分标准，不可拿去训练背诵。

## 运行与依赖

优先通过Codex的工作区依赖工具取得配套Python。其他机器使用Python 3.9+及 `python-docx`。如缺库，在独立虚拟环境安装，避免改用户全局环境：

```bash
python3 -m venv /工作目录/.venv
/工作目录/.venv/bin/python -m pip install python-docx
```

从技能目录执行：

```bash
python3 scripts/build_report.py /工作目录/grading.json --validate-only
python3 scripts/build_report.py /工作目录/grading.json /工作目录/刑法案例分析阅卷.docx
python3 scripts/self_test.py
```

使用绝对输出路径。输出目标已存在时，先确认是本次报告再覆盖，或另取文件名。脚本支持直接覆盖以便同次排版修订，不自动修改原始材料。

## 版式及验收

A4、正文11pt、左右两列适当分宽。字体默认macOS宋体Songti SC、Windows SimSun、Linux Noto Serif CJK SC，也可通过 `--font '已安装的中文字体名'` 指定。每案例一张表，每问一行，跨页重复表头；长行允许续页，不缩小到难以阅读。左栏只放原文及颜色/删除线，右栏显示该问采分情况。深绿006400、红C00000、蓝0070C0，均为真实Word字色，非图片或HTML伪标签。示范答案另起小标题，使用黑色。

结构校验不代替判断和视觉检查。用可用的文档渲染工具输出所有页，逐页查看字体、表格、原文换行、颜色和续页，防截断。若用documents技能及其配套LibreOffice，只用工作区依赖工具返回的bundled版本，不调用用户桌面LibreOffice。无法渲染时说明限制。

### macOS预览只有数字、中文空白时

这可能是headless渲染器未加载系统中文字体，不能仅凭进程成功退出判定文档正常。无需改用户全局配置，可在本次工作目录建立临时 `fonts.conf`：

```xml
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>/System/Library/Fonts</dir>
  <dir>/System/Library/Fonts/Supplemental</dir>
  <dir>/Library/Fonts</dir>
  <cachedir>/tmp/criminal-case-grader-font-cache</cachedir>
</fontconfig>
```

仅在渲染命令的进程环境设置 `FONTCONFIG_FILE` 为该文件绝对路径、`SAL_PRIVATE_FONTPATH=/System/Library/Fonts/Supplemental`，再运行已解析的bundled渲染工具；不要更改HOME、系统字体、用户桌面LibreOffice或全局设置。已在macOS配套渲染器验证此恢复方式。重看所有页，不能把字体缺失当成考生未答。
