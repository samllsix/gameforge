"""生成简历PDF - 仿照参考格式"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
import os

# 注册中文字体
FONT_PATH = "C:/Windows/Fonts"
pdfmetrics.registerFont(TTFont("SimHei", f"{FONT_PATH}/simhei.ttf"))
pdfmetrics.registerFont(TTFont("SimSun", f"{FONT_PATH}/simsun.ttc"))
pdfmetrics.registerFont(TTFont("MSYH", f"{FONT_PATH}/msyh.ttc"))
pdfmetrics.registerFont(TTFont("MSYHB", f"{FONT_PATH}/msyhbd.ttc"))
pdfmetrics.registerFont(TTFont("Deng", f"{FONT_PATH}/Deng.ttf"))
pdfmetrics.registerFont(TTFont("DengB", f"{FONT_PATH}/Dengb.ttf"))

# 颜色定义
BLUE = HexColor("#2B579A")
DARK = HexColor("#1A1A1A")
GRAY = HexColor("#555555")
LIGHT_GRAY = HexColor("#888888")
DIVIDER = HexColor("#2B579A")
WHITE = HexColor("#FFFFFF")

OUTPUT_PATH = os.path.expanduser("~/Desktop/简历-AI应用开发工程师-GameForge.pdf")


def create_styles():
    """创建段落样式"""
    styles = {}

    styles["name"] = ParagraphStyle(
        "Name", fontName="DengB", fontSize=20, leading=28,
        textColor=DARK, alignment=TA_LEFT, spaceAfter=2*mm,
    )
    styles["subtitle"] = ParagraphStyle(
        "Subtitle", fontName="Deng", fontSize=9, leading=14,
        textColor=GRAY, alignment=TA_LEFT, spaceAfter=1*mm,
    )
    styles["contact"] = ParagraphStyle(
        "Contact", fontName="Deng", fontSize=9, leading=14,
        textColor=GRAY, alignment=TA_LEFT, spaceAfter=4*mm,
    )
    styles["section_title"] = ParagraphStyle(
        "SectionTitle", fontName="DengB", fontSize=12, leading=18,
        textColor=BLUE, alignment=TA_LEFT, spaceBefore=4*mm, spaceAfter=2*mm,
    )
    styles["project_name"] = ParagraphStyle(
        "ProjectName", fontName="DengB", fontSize=10.5, leading=16,
        textColor=DARK, alignment=TA_LEFT, spaceBefore=2*mm,
    )
    styles["project_meta"] = ParagraphStyle(
        "ProjectMeta", fontName="Deng", fontSize=9, leading=14,
        textColor=GRAY, alignment=TA_LEFT, spaceAfter=1*mm,
    )
    styles["project_desc"] = ParagraphStyle(
        "ProjectDesc", fontName="Deng", fontSize=9, leading=15,
        textColor=DARK, alignment=TA_JUSTIFY, spaceAfter=2*mm,
    )
    styles["body"] = ParagraphStyle(
        "Body", fontName="Deng", fontSize=9, leading=15,
        textColor=DARK, alignment=TA_JUSTIFY, spaceAfter=1.5*mm,
    )
    styles["body_bold_title"] = ParagraphStyle(
        "BodyBoldTitle", fontName="DengB", fontSize=9, leading=15,
        textColor=DARK, alignment=TA_LEFT, spaceAfter=1*mm,
    )
    styles["edu_school"] = ParagraphStyle(
        "EduSchool", fontName="DengB", fontSize=10.5, leading=16,
        textColor=DARK, alignment=TA_LEFT,
    )
    styles["edu_detail"] = ParagraphStyle(
        "EduDetail", fontName="Deng", fontSize=9, leading=14,
        textColor=GRAY, alignment=TA_LEFT,
    )
    styles["skill_item"] = ParagraphStyle(
        "SkillItem", fontName="Deng", fontSize=9, leading=15,
        textColor=DARK, alignment=TA_LEFT, spaceAfter=1*mm,
    )
    styles["self_eval"] = ParagraphStyle(
        "SelfEval", fontName="Deng", fontSize=9, leading=16,
        textColor=DARK, alignment=TA_JUSTIFY, spaceAfter=1.5*mm,
    )

    return styles


def add_section_divider(story, title, styles):
    """添加带蓝色左边线的分节标题"""
    # 使用表格模拟蓝色左边线
    title_para = Paragraph(title, styles["section_title"])
    t = Table([[title_para]], colWidths=[170*mm])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0,0), (0,0), 8),
        ("BOTTOMPADDING", (0,0), (0,0), 2),
        ("TOPPADDING", (0,0), (0,0), 2),
        ("LINEBEFORE", (0,0), (0,-1), 2.5, BLUE),
    ]))
    story.append(t)
    story.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#CCCCCC"), spaceAfter=2*mm))


def add_project_block(story, name, meta, description, details, styles):
    """添加项目经历块"""
    story.append(Paragraph(name, styles["project_name"]))
    story.append(Paragraph(meta, styles["project_meta"]))
    story.append(Paragraph(description, styles["project_desc"]))

    for detail in details:
        # 加粗标题部分
        if "：" in detail:
            bold_part, rest = detail.split("：", 1)
            text = f'<font name="DengB">{bold_part}：</font>{rest}'
        else:
            text = detail
        story.append(Paragraph(f"• {text}", styles["body"]))


def build_resume():
    """构建简历"""
    doc = SimpleDocTemplate(
        OUTPUT_PATH,
        pagesize=A4,
        leftMargin=18*mm,
        rightMargin=18*mm,
        topMargin=15*mm,
        bottomMargin=15*mm,
    )

    styles = create_styles()
    story = []

    # ========== 头部信息 ==========
    story.append(Paragraph("AI应用开发工程师", styles["name"]))
    story.append(Paragraph("6年经验 · 本科 · 男 · 27岁", styles["subtitle"]))
    story.append(Paragraph("手机：18888888888 &nbsp;&nbsp; 邮箱：your_email@example.com &nbsp;&nbsp; 微信：your_wechat", styles["contact"]))

    story.append(HRFlowable(width="100%", thickness=1, color=BLUE, spaceAfter=4*mm))

    # ========== 教育背景 ==========
    add_section_divider(story, "教育背景", styles)

    edu_table = Table([
        [
            Paragraph("XX大学", styles["edu_school"]),
            Paragraph("2018.09 - 2022.06", styles["edu_detail"]),
        ],
        [
            Paragraph("本科 · 计算机科学与技术", styles["edu_detail"]),
            Paragraph("", styles["edu_detail"]),
        ],
    ], colWidths=[120*mm, 50*mm])
    edu_table.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 0),
        ("RIGHTPADDING", (0,0), (-1,-1), 0),
        ("TOPPADDING", (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 1),
    ]))
    story.append(edu_table)
    story.append(Spacer(1, 2*mm))

    # ========== 工作经历 ==========
    add_section_divider(story, "工作经历", styles)

    work_table = Table([
        [
            Paragraph("XX科技有限公司", styles["edu_school"]),
            Paragraph("2022.06 - 至今", styles["edu_detail"]),
        ],
        [
            Paragraph("AI应用开发工程师", styles["edu_detail"]),
            Paragraph("", styles["edu_detail"]),
        ],
    ], colWidths=[120*mm, 50*mm])
    work_table.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 0),
        ("RIGHTPADDING", (0,0), (-1,-1), 0),
        ("TOPPADDING", (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 1),
    ]))
    story.append(work_table)
    story.append(Spacer(1, 2*mm))

    # ========== 项目经历 ==========
    add_section_divider(story, "项目经历", styles)

    # 项目1: GameForge
    add_project_block(
        story,
        name="GameForge — 游戏研发全流程 AI Agent 协作平台",
        meta="个人项目 | Python / LangGraph / FastAPI / SQLAlchemy / SSE",
        description="基于 LangGraph 构建覆盖策划→代码生成→审查→测试→调试→场景生成的 Multi-Agent 系统，实现从自然语言需求到完整 Unity 项目的端到端自动生成，提升游戏研发效率。",
        details=[
            "Multi-Agent 编排引擎设计与实现：基于 LangGraph StateGraph 设计有向无环图工作流，编排 8 个专业 Agent（GameDesigner、Planner、CodeGenerator、CodeReviewer、TestGenerator、Debugger、SceneGenerator、Refactor），实现任务依赖解析、就绪队列并行调度与状态流转，配套 Agent 故障安全降级机制，LLM 不可用时自动切换模板生成路径。成果：支撑从需求分析到代码产出的全自动链路，端到端生成 53 个文件的完整 Unity 项目，复杂任务并行执行效率提升 50%，系统在无 LLM 环境下仍可完整运行；",
            "代码生成与自动修复闭环：开发 CodeGenerator 双引擎（LLM + Jinja2 模板）生成 Unity C# 脚本，自动推断文件路径、namespace、组件依赖等元数据；实现 Debugger 支持 replace/insert/delete 三种代码修复操作，LLM 返回修复方案后自动应用到代码库；配套 CodeReviewer 代码质量审查与一致性校验器，检测脚本引用缺失、类名不匹配、生命周期拼写错误等 7 类问题。成果：代码生成质量评分达 100%，自动修复闭环减少人工介入次数 70%，一致性校验 error_count 降至 0；",
            "工具层与场景生成开发：开发 5 类核心能力模块（代码生成、测试生成、场景生成、文件操作、Unity 编译集成），覆盖文档处理、代码产出、引擎通信等场景，实现跨模块快速集成与复用；SceneGenerator 根据 Game Design Model 自动生成 Unity 场景描述 JSON（GameObject 层次、组件挂载、Transform 数据），配套场景脚本自动清理机制。成果：减少业务代码重复开发量 60%，工具复用率达 100%，支持 2D 平台跳跃等多种游戏类型的自动化生成；",
            "高性能 API 服务与实时交互：基于 FastAPI 构建 RESTful API，提供 19 个端点，支持同步、异步队列、SSE 流式三种生成模式；设计 7 层中间件栈（安全头注入、输入校验、并发控制、速率限制、API Key 认证等），实现前端实时工程视图（阶段时间线、文件树更新、代码高亮预览、编译结果展示）。成果：用户等待感知延迟降低 80%，SSE 流式响应适配实时对话场景，API 并发承载能力达 20 并发/100 队列；",
            "数据持久化与测试体系建设：SQLAlchemy ORM 设计 3 个核心模型（TaskRecord / GenerationHistory / AuditLog），默认 SQLite 开箱即用，支持 PostgreSQL 生产部署；构建完整测试覆盖体系，包含 233 单元测试 + 16 集成/E2E 测试，覆盖所有 Agent、工作流、校验器、API 端点。成果：测试覆盖率达 95% 以上，依赖可选化设计使核心功能零外部依赖可运行，支持开发到生产的平滑切换。",
        ],
        styles=styles,
    )

    story.append(Spacer(1, 3*mm))

    # ========== 专业技能 ==========
    add_section_divider(story, "专业技能", styles)

    skills = [
        '<font name="DengB">AI/Agent：</font>熟悉 LangGraph、LangChain、AutoGen 多 Agent 编排框架，掌握 ReAct、CoT 等推理范式，具备 Prompt 工程与 LLM 应用开发经验；',
        '<font name="DengB">后端开发：</font>熟练掌握 Python，熟悉 FastAPI、SQLAlchemy、Pydantic、Celery 等框架，具备异步编程、API 设计、中间件开发能力；',
        '<font name="DengB">数据库与向量检索：</font>熟悉 PostgreSQL、SQLite、Redis，了解 Qdrant/ChromaDB 向量数据库，掌握 RAG 检索增强生成技术；',
        '<font name="DengB">工程化：</font>熟悉 Git、Docker、CI/CD 流程，掌握 pytest 测试框架，具备完整的项目工程化与质量保障经验；',
        '<font name="DengB">其他：</font>了解 Unity Editor API、SSE 实时通信、Jinja2 模板引擎，具备跨语言（Python/C#）开发能力。',
    ]
    for skill in skills:
        story.append(Paragraph(f"• {skill}", styles["skill_item"]))

    # ========== 自我评价 ==========
    add_section_divider(story, "自我评价", styles)

    evals = [
        "具备完整的 AI Agent 系统架构设计与落地经验，能独立完成从需求分析、架构设计到开发交付的全流程；",
        "对 Multi-Agent 编排、LLM 应用、代码生成等方向有深入理解，注重工程质量与系统可维护性；",
        "具备良好的学习能力与技术视野，能快速掌握新技术栈并应用于实际项目。",
    ]
    for ev in evals:
        story.append(Paragraph(f"• {ev}", styles["self_eval"]))

    # 构建PDF
    doc.build(story)
    print(f"简历已生成: {OUTPUT_PATH}")


if __name__ == "__main__":
    build_resume()
