//! Every knob a `.lomt` file exposes: `choose` dimensions, user switches,
//! capabilities, `const` values and `loment.conf` keys — parsed into a catalog
//! the CLI and the web UI can edit.
//!
//! Edits are surgical text splices, never a re-print of the file: comments,
//! blank lines and formatting survive. That matters because these are the
//! compiler's own sources.

pub mod catalog;
pub mod matrix;

use std::collections::HashMap;
use std::path::Path;

use serde::{Deserialize, Serialize};

pub use catalog::Catalog;

pub const GROUP_DIMS: &str = "语言环境";
pub const GROUP_SWITCH: &str = "自定义开关";
pub const GROUP_CAP: &str = "能力域";
pub const GROUP_CONST: &str = "常量";
pub const GROUP_CONF: &str = "项目配置";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ItemKind {
    Choice,
    Switch,
    Capability,
    Const,
    Conf,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Item {
    pub id: String,
    pub group: String,
    pub name: String,
    /// Display label; for capability sub-fields it is `name · field`.
    #[serde(default)]
    pub label: String,
    pub value: String,
    pub default: Option<String>,
    pub help: String,
    pub line: usize,
    pub kind: ItemKind,
    #[serde(default)]
    pub ty: String,
    #[serde(default)]
    pub options: Vec<String>,
    #[serde(default)]
    pub written: bool,
    /// For constants: how many other lines mention the name.
    #[serde(default)]
    pub refs: usize,
    #[serde(default)]
    pub unit: Option<String>,
    #[serde(default)]
    pub min: Option<f64>,
    #[serde(default)]
    pub max: Option<f64>,
    /// A caution from the catalog (e.g. "越界是段错误").
    #[serde(default)]
    pub note: Option<String>,
    /// The source's own comment, kept when the catalog overrides `help`.
    #[serde(default)]
    pub src_help: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Change {
    pub line: usize,
    pub before: Option<String>,
    pub after: Option<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct DimSpec {
    dim: &'static str,
    name: &'static str,
    values: &'static [(&'static str, &'static str)],
    default: &'static str,
    help: &'static str,
}

pub(crate) const DIMS: &[DimSpec] = &[
    DimSpec {
        dim: "mode",
        name: "运行模式",
        values: &[
            ("std", "标准库可用（默认）"),
            ("no_std", "只用核那一层，不链标准库"),
        ],
        default: "std",
        help: "决定产物能不能用标准库。`no_std` 的项目只能用核提供的那一层；\
               它与自动回收冲突（自动回收需要运行期，而 `no_std` 的定义是核保持小）。",
    },
    DimSpec {
        dim: "gc",
        name: "回收档",
        values: &[
            ("gc_manual", "手动管理（默认）"),
            ("gc_auto", "自动回收，需要运行期"),
            ("gc_auto_alpha", "混合档：静态管理 + 动态回收，实验性"),
        ],
        default: "gc_manual",
        help: "决定内存怎么还。手动档由代码自己 `free`；自动档要 `choose runtime`；\
               `alpha` 档没有冻结全部业务的阶段，行为仍在动。",
    },
    DimSpec {
        dim: "runtime",
        name: "运行期",
        values: &[
            ("no_runtime", "产物里不带运行期（默认）"),
            ("runtime", "带运行期：自动回收、线程、根表需要它"),
        ],
        default: "no_runtime",
        help: "运行期是自动回收和线程的宿主。默认关；打开后产物会大一些，\
               但 `alloc` 与自动回收才有地方住。",
    },
    DimSpec {
        dim: "grammar",
        name: "写语法",
        values: &[
            ("loment", "Loment 原生写法（默认）"),
            ("python", "Python 风格，前门翻译成 Loment"),
            ("c", "C 风格"),
            ("cpp", "C++ 风格"),
            ("java", "Java 风格"),
            ("cs", "C# 风格"),
            ("go", "Go 风格"),
        ],
        default: "loment",
        help: "这份文件用哪种表层语法写。`choose write grammar X` 必须写在 `module` 之前；\
               六门表层语法共享同一套语义。",
    },
];

fn dim_spec(dim: &str) -> Option<&'static DimSpec> {
    DIMS.iter().find(|d| d.dim == dim)
}

#[derive(Clone, Debug, Default)]
struct ChoiceLoc {
    line: usize,
    value: String,
}

#[derive(Clone, Debug, Default)]
struct SwitchLoc {
    value_line: Option<usize>,
    def_line: usize,
    on: bool,
}

#[derive(Clone, Debug, Default)]
struct ConstLoc {
    line: usize,
    start: usize,
    end: usize,
    ty: String,
}

#[derive(Clone, Debug, Default)]
struct CapLoc {
    line: usize,
    space: (usize, usize),
    lo: (usize, usize),
    hi: (usize, usize),
    rev: Option<(usize, usize)>,
}

#[derive(Clone, Debug, Default)]
struct ConfLoc {
    line: usize,
    start: usize,
    end: usize,
    ty: String,
}

#[derive(Clone, Debug)]
pub struct Doc {
    pub path: String,
    pub text: String,
    pub items: Vec<Item>,
    choices: HashMap<String, ChoiceLoc>,
    switches: HashMap<String, SwitchLoc>,
    consts: HashMap<String, ConstLoc>,
    caps: HashMap<String, CapLoc>,
    conf: HashMap<String, ConfLoc>,
    module_line: Option<usize>,
}

impl Doc {
    pub fn line_count(&self) -> usize {
        self.text.lines().count()
    }

    pub fn item(&self, id: &str) -> Option<&Item> {
        self.items.iter().find(|i| i.id == id)
    }

    fn first_line_starting(&self, prefix: &str) -> Option<usize> {
        self.text
            .lines()
            .position(|l| l.trim_start().starts_with(prefix))
    }
}

pub fn parse(path: impl Into<String>, text: &str) -> Doc {
    parse_with(path, text, &Catalog::default())
}

pub fn parse_with(path: impl Into<String>, text: &str, cat: &Catalog) -> Doc {
    let lines: Vec<String> = text.lines().map(str::to_string).collect();
    let mut doc = Doc {
        path: path.into(),
        text: text.to_string(),
        items: Vec::new(),
        choices: HashMap::new(),
        switches: HashMap::new(),
        consts: HashMap::new(),
        caps: HashMap::new(),
        conf: HashMap::new(),
        module_line: None,
    };

    // Pass 1: switch definitions (`set choose X {`).
    let mut depth = 0i32;
    for (i, line) in lines.iter().enumerate() {
        if depth == 0 {
            if let Some(rest) = line.trim().strip_prefix("set choose ") {
                if let Some(name) = rest.split(['{', ' ', '\t']).next() {
                    if !name.is_empty() {
                        doc.switches.entry(name.to_string()).or_insert(SwitchLoc {
                            value_line: None,
                            def_line: i,
                            on: false,
                        });
                    }
                }
            }
        }
        depth += brace_delta(line);
    }

    // Pass 2: values, declarations, module.
    depth = 0;
    for (i, line) in lines.iter().enumerate() {
        let t = line.trim();
        if depth == 0 {
            if t.starts_with("module ") && doc.module_line.is_none() {
                doc.module_line = Some(i);
            }
            if let Some(rest) = t.strip_prefix("choose ") {
                parse_choose(&mut doc, i, rest);
            }
            if let Some((name, ty, start, end, comment)) = parse_const(line) {
                let refs = count_refs(&lines, i, &name);
                let source_help = if !comment.is_empty() {
                    Some(comment)
                } else {
                    preceding_comment(&lines, i)
                };
                let help = source_help.clone().unwrap_or_else(|| {
                    if refs == 0 {
                        format!("{ty} 类型常量；这份文件里没有别处引用它，可能是给别的单元用的。")
                    } else {
                        format!("{ty} 类型常量；这份文件里被引用 {refs} 次。源码没有说明，改之前先看那些引用点。")
                    }
                });
                doc.consts.insert(name.clone(), ConstLoc { line: i, start, end, ty: ty.clone() });
                doc.items.push(Item {
                    id: format!("const:{name}"),
                    group: GROUP_CONST.into(),
                    label: name.clone(),
                    name,
                    value: line[start..end].trim().to_string(),
                    default: None,
                    help,
                    line: i + 1,
                    kind: ItemKind::Const,
                    ty,
                    options: Vec::new(),
                    written: true,
                    refs,
                    unit: None,
                    min: None,
                    max: None,
                    note: None,
                    src_help: source_help,
                });
            }
            if let Some(cap) = parse_capability(line) {
                let mut loc = cap.loc.clone();
                loc.line = i;
                doc.caps.insert(cap.name.clone(), loc);
                let point = cap.lo == cap.hi;
                // docs/306: a domain is space × shape × polarity × time law. Today's
                // syntax only spells a closed interval (allow, no time law), so the
                // simplest domain — a point — is lo == hi.
                let lo_help = if point {
                    format!(
                        "点域：上下界相同（{}），只有这一个下标能通过。docs/306 §2.2 的五种形状里最简单的一种。",
                        cap.lo
                    )
                } else {
                    "区间下界。两端的数一起定义「这个能力能碰哪些下标」。".to_string()
                };
                let hi_help = if point {
                    "点域的另一端，与下界相同。把它改大就变成常规域 [a..b]。".to_string()
                } else {
                    "区间上界（包含）。改小时要确认没有代码还在用更大的下标。".to_string()
                };
                for (field, value, help, ty) in [
                    ("space", cap.space.clone(), "资源空间的名字（`disk` / `mem` / 内核定义的其它空间）。docs/306 把域拆成 空间 × 形状 × 极性 × 时律，空间是最外层。".to_string(), ""),
                    ("lo", cap.lo.clone(), lo_help, "u32"),
                    ("hi", cap.hi.clone(), hi_help, "u32"),
                    ("revocable", if cap.loc.rev.is_some() { "true".into() } else { "false".into() }, "可撤销：持有者能在运行期被收回这项能力。docs/306 把它归入时律的一个终态，而不是独立的第四维。".to_string(), "bool"),
                ] {
                    doc.items.push(Item {
                        id: format!("cap:{}:{}", cap.name, field),
                        group: GROUP_CAP.into(),
                        label: format!("{} · {}", cap.name, field),
                        name: cap.name.clone(),
                        value,
                        default: Some(if field == "revocable" { "false".into() } else { String::new() }),
                        help,
                        line: i + 1,
                        kind: ItemKind::Capability,
                        ty: ty.into(),
                        options: Vec::new(),
                        written: true,
                        refs: 0,
                        unit: None,
                        min: None,
                        max: None,
                        note: None,
                        src_help: None,
                    });
                }
            }
            if let Some((key, ty, start, end)) = parse_conf_line(line) {
                doc.conf.insert(key.clone(), ConfLoc { line: i, start, end, ty: ty.clone() });
                doc.items.push(Item {
                    id: format!("conf:{key}"),
                    group: GROUP_CONF.into(),
                    label: key.clone(),
                    name: key,
                    value: line[start..end].trim().to_string(),
                    default: None,
                    help: "项目配置项：`loment.conf` 里的一条声明，工具链按名字读它。".into(),
                    line: i + 1,
                    kind: ItemKind::Conf,
                    ty,
                    options: Vec::new(),
                    written: true,
                    refs: 0,
                    unit: None,
                    min: None,
                    max: None,
                    note: None,
                    src_help: None,
                });
            }
        }
        depth += brace_delta(line);
    }

    // Dimensions: always listed, written or not.
    for spec in DIMS {
        let loc = doc.choices.get(spec.dim);
        let value = loc.map(|c| c.value.clone()).unwrap_or_else(|| spec.default.to_string());
        let values = spec.values.iter().map(|(v, note)| format!("{v} — {note}")).collect();
        doc.items.insert(
            dim_order(spec.dim),
            Item {
                id: spec.dim.into(),
                group: GROUP_DIMS.into(),
                label: spec.name.into(),
                name: spec.name.into(),
                value,
                default: Some(spec.default.into()),
                help: spec.help.into(),
                line: loc.map(|c| c.line + 1).unwrap_or(0),
                kind: ItemKind::Choice,
                ty: String::new(),
                options: values,
                written: loc.is_some(),
                refs: 0,
                unit: None,
                min: None,
                max: None,
                note: None,
                src_help: None,
            },
        );
    }

    // Switches.
    let mut switches: Vec<_> = doc.switches.iter().collect();
    switches.sort_by(|a, b| a.0.cmp(b.0));
    for (name, loc) in switches {
        doc.items.push(Item {
            id: format!("switch:{name}"),
            group: GROUP_SWITCH.into(),
            label: name.clone(),
            name: name.clone(),
            value: if loc.on { "on".into() } else { "off".into() },
            default: Some("off".into()),
            help: format!(
                "源码用 `set choose {name} {{ … }}` 定义的开关。打开它，那段代码才会被编进产物；\
                 关着时代码不参与编译。"
            ),
            line: loc.value_line.map(|l| l + 1).unwrap_or(loc.def_line + 1),
            kind: ItemKind::Switch,
            ty: String::new(),
            options: vec!["on — 编进这段代码".into(), "off — 这段代码不参与编译".into()],
            written: loc.value_line.is_some(),
            refs: 0,
            unit: None,
            min: None,
            max: None,
            note: None,
            src_help: None,
        });
    }

    apply_catalog(&mut doc, cat);
    doc
}

fn apply_catalog(doc: &mut Doc, cat: &Catalog) {
    for item in &mut doc.items {
        let rule = match item.kind {
            ItemKind::Const => cat.for_const(&item.name),
            ItemKind::Switch => cat.for_switch(&item.name),
            ItemKind::Capability => cat.for_cap(&item.name),
            ItemKind::Conf => cat.for_conf(&item.name),
            ItemKind::Choice => None,
        };
        let Some(rule) = rule else { continue };
        if let Some(h) = &rule.help {
            item.help = h.clone();
        }
        if let Some(g) = &rule.group {
            item.group = g.clone();
        }
        item.unit = rule.unit.clone();
        item.min = rule.min;
        item.max = rule.max;
        item.note = rule.note.clone();
    }
}

fn dim_order(dim: &str) -> usize {
    DIMS.iter().position(|d| d.dim == dim).unwrap_or(0)
}

fn brace_delta(line: &str) -> i32 {
    let mut d = 0;
    let mut in_str = false;
    let mut prev = '\0';
    for c in line.chars() {
        if in_str {
            if c == '"' && prev != '\\' {
                in_str = false;
            }
        } else if c == '"' {
            in_str = true;
        } else if c == '{' {
            d += 1;
        } else if c == '}' {
            d -= 1;
        }
        prev = c;
    }
    d
}

fn parse_choose(doc: &mut Doc, line: usize, rest: &str) {
    let parts: Vec<&str> = rest.split_whitespace().collect();
    if parts.is_empty() {
        return;
    }
    if parts[0] == "write" && parts.get(1) == Some(&"grammar") {
        if let Some(v) = parts.get(2) {
            doc.choices.insert("grammar".into(), ChoiceLoc { line, value: (*v).to_string() });
        }
        return;
    }
    if parts[0] == "close" {
        if let Some(name) = parts.get(1) {
            if let Some(s) = doc.switches.get_mut(*name) {
                s.on = false;
                s.value_line = Some(line);
            }
        }
        return;
    }
    let word = parts[0];
    let dim = DIMS
        .iter()
        .find(|d| d.values.iter().any(|(v, _)| *v == word) && d.dim != "grammar")
        .map(|d| d.dim);
    if let Some(dim) = dim {
        doc.choices.insert(dim.into(), ChoiceLoc { line, value: word.into() });
        return;
    }
    if let Some(s) = doc.switches.get_mut(word) {
        s.on = true;
        s.value_line = Some(line);
    }
}

struct ParsedCap {
    name: String,
    space: String,
    lo: String,
    hi: String,
    loc: CapLoc,
}

/// `capability NAME : SPACE[LO..HI] revocable`
fn parse_capability(line: &str) -> Option<ParsedCap> {
    let t = line.trim_start();
    let rest = t.strip_prefix("capability ")?;
    let base = line.len() - t.len() + "capability ".len();
    let colon = rest.find(':')?;
    let name = rest[..colon].trim().to_string();
    if name.is_empty() {
        return None;
    }
    let abs_colon = base + colon;
    let open = line[abs_colon..].find('[')? + abs_colon;
    let close = line[open..].find(']')? + open;
    let space_raw = &line[abs_colon + 1..open];
    let space = space_raw.trim().to_string();
    if space.is_empty() {
        return None;
    }
    let space_start = abs_colon + 1 + (space_raw.len() - space_raw.trim_start().len());
    let space_end = space_start + space.len();
    let inner = &line[open + 1..close];
    let dots = inner.find("..")?;
    let lo_raw = &inner[..dots];
    let hi_raw = &inner[dots + 2..];
    let lo = lo_raw.trim().to_string();
    let hi = hi_raw.trim().to_string();
    let lo_len = lo.len();
    let hi_len = hi.len();
    let lo_start = open + 1 + (lo_raw.len() - lo_raw.trim_start().len());
    let hi_start = open + 1 + dots + 2 + (hi_raw.len() - hi_raw.trim_start().len());
    let rev_start = line[close + 1..].find("revocable").map(|p| close + 1 + p);
    Some(ParsedCap {
        name,
        space,
        lo,
        hi,
        loc: CapLoc {
            line: 0,
            space: (space_start, space_end),
            lo: (lo_start, lo_start + lo_len),
            hi: (hi_start, hi_start + hi_len),
            rev: rev_start.map(|s| (s, s + "revocable".len())),
        },
    })
}

/// One-line `pub fn key() -> ty { return value; }` (the `loment.conf` shape).
fn parse_conf_line(line: &str) -> Option<(String, String, usize, usize)> {
    let t = line.trim_start();
    let rest = t.strip_prefix("pub fn ")?;
    let paren = rest.find('(')?;
    let key = rest[..paren].trim().to_string();
    if key.is_empty() {
        return None;
    }
    let arrow = rest.find("->")?;
    let brace = rest.find('{')?;
    if brace < arrow {
        return None;
    }
    let ty = rest[arrow + 2..brace].trim().to_string();
    let body = &rest[brace + 1..];
    let ret = body.find("return ")?;
    let semi = body[ret..].find(';')? + ret;
    let base = line.len() - t.len() + "pub fn ".len();
    let value_raw = &body[ret + "return ".len()..semi];
    let value = value_raw.trim();
    if value.is_empty() {
        return None;
    }
    let start = base + brace + 1 + ret + "return ".len() + (value_raw.len() - value_raw.trim_start().len());
    Some((key, ty, start, start + value.len()))
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Edit {
    pub id: String,
    pub value: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Applied {
    pub text: String,
    pub changes: Vec<Change>,
}

pub fn apply(doc: &Doc, edits: &[Edit]) -> Result<Applied, String> {
    let mut text = doc.text.clone();
    let mut changes: Vec<Change> = Vec::new();
    for edit in edits {
        let current = parse(doc.path.clone(), &text);
        let item = current
            .item(&edit.id)
            .cloned()
            .ok_or_else(|| format!("没有这个参数: {}", edit.id))?;
        let (new_text, change) = match item.kind {
            ItemKind::Choice => set_choice(&current, &edit.id, &edit.value)?,
            ItemKind::Switch => set_switch(&current, &item, &edit.value)?,
            ItemKind::Const => set_const(&current, &item, &edit.value)?,
            ItemKind::Capability => set_cap(&current, &item, &edit.value)?,
            ItemKind::Conf => set_conf(&current, &item, &edit.value)?,
        };
        if new_text != text {
            changes.push(change);
            text = new_text;
        }
    }
    Ok(Applied { text, changes })
}

fn replace_line(lines: &mut [String], idx: usize, new_line: String) -> Change {
    let before = lines[idx].clone();
    lines[idx] = new_line.clone();
    Change { line: idx + 1, before: Some(before), after: Some(new_line) }
}

fn insert_line(lines: &mut Vec<String>, idx: usize, new_line: String) -> Change {
    lines.insert(idx, new_line.clone());
    Change { line: idx + 1, before: None, after: Some(new_line) }
}

fn join(lines: &[String], had_trailing_newline: bool) -> String {
    let mut s = lines.join("\n");
    if had_trailing_newline {
        s.push('\n');
    }
    s
}

fn set_choice(doc: &Doc, dim: &str, value: &str) -> Result<(String, Change), String> {
    let spec = dim_spec(dim).ok_or_else(|| format!("未知维度: {dim}"))?;
    if !spec.values.iter().any(|(v, _)| *v == value) {
        return Err(format!(
            "{dim} 只能是 {}",
            spec.values.iter().map(|(v, _)| *v).collect::<Vec<_>>().join(" / ")
        ));
    }
    let directive = if dim == "grammar" {
        format!("choose write grammar {value}")
    } else {
        format!("choose {value}")
    };
    let mut lines: Vec<String> = doc.text.lines().map(str::to_string).collect();
    let trailing = doc.text.ends_with('\n');
    let change = if let Some(loc) = doc.choices.get(dim) {
        let indent = leading_ws(&lines[loc.line]);
        replace_line(&mut lines, loc.line, format!("{indent}{directive}"))
    } else if dim == "grammar" {
        let at = doc.module_line.unwrap_or(0);
        insert_line(&mut lines, at, directive)
    } else {
        let at = doc.module_line.map(|m| m + 1).unwrap_or(0);
        insert_line(&mut lines, at, directive)
    };
    Ok((join(&lines, trailing), change))
}

fn set_switch(doc: &Doc, item: &Item, value: &str) -> Result<(String, Change), String> {
    let on = match value {
        "on" | "true" | "1" => true,
        "off" | "false" | "0" => false,
        other => return Err(format!("开关只能是 on/off，得到 {other}")),
    };
    let name = item.name.as_str();
    let loc = doc.switches.get(name).ok_or_else(|| format!("没有这个开关: {name}"))?;
    let mut lines: Vec<String> = doc.text.lines().map(str::to_string).collect();
    let trailing = doc.text.ends_with('\n');
    let directive = if on { format!("choose {name}") } else { format!("choose close {name}") };
    let change = if let Some(line) = loc.value_line {
        let indent = leading_ws(&lines[line]);
        replace_line(&mut lines, line, format!("{indent}{directive}"))
    } else {
        let mut depth = 0i32;
        let mut end = loc.def_line;
        for i in loc.def_line..lines.len() {
            depth += brace_delta(&lines[i]);
            if depth <= 0 && (i > loc.def_line || lines[i].contains('}')) {
                end = i;
                break;
            }
        }
        insert_line(&mut lines, end + 1, directive)
    };
    Ok((join(&lines, trailing), change))
}

fn set_const(doc: &Doc, item: &Item, value: &str) -> Result<(String, Change), String> {
    let name = item.name.as_str();
    let loc = doc.consts.get(name).ok_or_else(|| format!("没有这个常量: {name}"))?;
    let normalized = normalize_const_value(&loc.ty, value)?;
    let mut lines: Vec<String> = doc.text.lines().map(str::to_string).collect();
    let trailing = doc.text.ends_with('\n');
    let line = &lines[loc.line];
    let new_line = format!("{}{}{}", &line[..loc.start], normalized, &line[loc.end..]);
    let change = replace_line(&mut lines, loc.line, new_line);
    Ok((join(&lines, trailing), change))
}

fn set_cap(doc: &Doc, item: &Item, value: &str) -> Result<(String, Change), String> {
    let name = item.name.as_str();
    let loc = doc.caps.get(name).ok_or_else(|| format!("没有这个能力域: {name}"))?;
    let field = item.id.rsplit(':').next().unwrap_or("");
    let mut lines: Vec<String> = doc.text.lines().map(str::to_string).collect();
    let trailing = doc.text.ends_with('\n');
    let line = lines[loc.line].clone();
    let new_line = match field {
        "space" => {
            let v = value.trim();
            if !is_ident(v) {
                return Err("空间名要写成标识符".into());
            }
            splice(&line, loc.space, v)
        }
        "lo" | "hi" => {
            let v = value.trim();
            if v.is_empty() || !v.chars().all(|c| c.is_ascii_digit()) {
                return Err("区间端点要写成非负整数".into());
            }
            splice(&line, if field == "lo" { loc.lo } else { loc.hi }, v)
        }
        "revocable" => {
            let on = matches!(value, "true" | "on" | "1");
            if on {
                if loc.rev.is_some() {
                    line
                } else {
                    format!("{} revocable", line.trim_end())
                }
            } else if let Some((s, e)) = loc.rev {
                let mut cut = s;
                while cut > 0 && line.as_bytes()[cut - 1] == b' ' {
                    cut -= 1;
                }
                format!("{}{}", &line[..cut], &line[e..])
            } else {
                line
            }
        }
        _ => return Err(format!("未知能力域字段: {field}")),
    };
    let change = replace_line(&mut lines, loc.line, new_line);
    Ok((join(&lines, trailing), change))
}

fn set_conf(doc: &Doc, item: &Item, value: &str) -> Result<(String, Change), String> {
    let key = item.name.as_str();
    let loc = doc.conf.get(key).ok_or_else(|| format!("没有这个配置项: {key}"))?;
    let normalized = normalize_const_value(&loc.ty, value)?;
    let mut lines: Vec<String> = doc.text.lines().map(str::to_string).collect();
    let trailing = doc.text.ends_with('\n');
    let line = &lines[loc.line];
    let new_line = format!("{}{}{}", &line[..loc.start], normalized, &line[loc.end..]);
    let change = replace_line(&mut lines, loc.line, new_line);
    Ok((join(&lines, trailing), change))
}

fn splice(line: &str, span: (usize, usize), value: &str) -> String {
    format!("{}{}{}", &line[..span.0], value, &line[span.1..])
}

/// Loment `const` values are integer literals only (`parse_const` calls `int_lit`).
fn normalize_int(value: &str) -> Result<String, String> {
    let v = value.trim();
    let digits = v.strip_prefix('-').unwrap_or(v);
    if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_digit()) {
        return Err(format!("常量值必须是整数，得到 {v}"));
    }
    Ok(v.into())
}

fn normalize_const_value(ty: &str, value: &str) -> Result<String, String> {
    let v = value.trim();
    match ty {
        "bool" => match v {
            "true" | "false" => Ok(v.into()),
            _ => Err("bool 值只能是 true / false".into()),
        },
        "str" => {
            if v.starts_with('"') && v.ends_with('"') {
                Ok(v.into())
            } else {
                Ok(format!("\"{}\"", v.replace('"', "\\\"")))
            }
        }
        _ if ty.starts_with('u') || ty.starts_with('i') => {
            let digits = v.trim_start_matches('-');
            if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_digit()) {
                return Err(format!("{ty} 要写成整数，得到 {v}"));
            }
            Ok(v.into())
        }
        _ => Ok(v.into()),
    }
}

/// A new top-level declaration should not butt against the next one.
fn ensure_blank_after(lines: &mut Vec<String>, idx: usize) {
    if idx < lines.len() && !lines[idx].trim().is_empty() && !lines[idx - 1].trim().is_empty() {
        let next = lines[idx].trim_start();
        if next.starts_with("fn ") || next.starts_with("capability ") || next.starts_with("pub fn ") {
            lines.insert(idx, String::new());
        }
    }
}

fn leading_ws(s: &str) -> String {
    s.chars().take_while(|c| c.is_whitespace()).collect()
}

fn is_ident(s: &str) -> bool {
    let mut chars = s.chars();
    match chars.next() {
        Some(c) if c == '_' || c.is_ascii_alphabetic() => {}
        _ => return false,
    }
    chars.all(|c| c == '_' || c.is_ascii_alphanumeric())
}

fn is_ident_char(c: char) -> bool {
    c == '_' || c.is_alphanumeric()
}

fn count_refs(lines: &[String], skip: usize, name: &str) -> usize {
    let mut n = 0;
    for (j, line) in lines.iter().enumerate() {
        if j == skip {
            continue;
        }
        let mut from = 0;
        while let Some(pos) = line[from..].find(name) {
            let at = from + pos;
            let before_ok = at == 0 || !line[..at].chars().next_back().map(is_ident_char).unwrap_or(false);
            let after = at + name.len();
            let after_ok = after >= line.len() || !line[after..].chars().next().map(is_ident_char).unwrap_or(false);
            if before_ok && after_ok {
                n += 1;
            }
            from = at + name.len();
            if from >= line.len() {
                break;
            }
        }
    }
    n
}

/// A `//` block directly above a declaration is the author's own documentation.
fn preceding_comment(lines: &[String], at: usize) -> Option<String> {
    let mut out = Vec::new();
    let mut j = at;
    while j > 0 {
        j -= 1;
        let t = lines[j].trim();
        if t.is_empty() {
            break;
        }
        let Some(c) = t.strip_prefix("///").or_else(|| t.strip_prefix("//")) else {
            break;
        };
        out.push(c.trim().to_string());
    }
    if out.is_empty() {
        None
    } else {
        out.reverse();
        Some(out.join(" "))
    }
}

/// `const NAME: TY = VALUE;` at depth 0 (also `pub const`).
fn parse_const(line: &str) -> Option<(String, String, usize, usize, String)> {
    let t = line.trim_start();
    let rest = t.strip_prefix("pub const ").or_else(|| t.strip_prefix("const "))?;
    let colon = rest.find(':')?;
    let name = rest[..colon].trim().to_string();
    if name.is_empty() {
        return None;
    }
    let after = &rest[colon + 1..];
    let eq = after.find('=')?;
    let ty = after[..eq].trim().to_string();
    let semi = after.rfind(';')?;
    if semi <= eq {
        return None;
    }
    let prefix = line.len() - rest.len();
    let abs_eq = prefix + colon + 1 + eq;
    let abs_semi = prefix + colon + 1 + semi;
    let raw = &line[abs_eq + 1..abs_semi];
    if raw.contains("//") {
        return None;
    }
    let lead = raw.len() - raw.trim_start().len();
    let trail = raw.len() - raw.trim_end().len();
    let comment = match line[abs_semi + 1..].find("//") {
        Some(p) => line[abs_semi + 1 + p + 2..].trim().to_string(),
        None => String::new(),
    };
    Some((name, ty, abs_eq + 1 + lead, abs_semi - trail, comment))
}

/* ---------------------------------------------------------------- adding */

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum NewParam {
    Const { name: String, ty: String, value: String, #[serde(default)] help: String },
    Switch { name: String },
    Capability { name: String, space: String, lo: u64, hi: u64, #[serde(default)] revocable: bool },
    Conf { key: String, #[serde(default = "conf_ty")] ty: String, value: String },
}

fn conf_ty() -> String {
    "str".into()
}

pub fn add(doc: &Doc, p: &NewParam) -> Result<Applied, String> {
    let mut lines: Vec<String> = doc.text.lines().map(str::to_string).collect();
    let trailing = doc.text.ends_with('\n');
    let change = match p {
        NewParam::Const { name, ty, value, help } => {
            if !is_ident(name) {
                return Err("常量名要是标识符".into());
            }
            if doc.consts.contains_key(name) {
                return Err(format!("常量 {name} 已经存在"));
            }
            let v = normalize_int(value)?;
            let comment = if help.trim().is_empty() { String::new() } else { format!("   // {}", help.trim()) };
            let text = format!("const {name}: {ty} = {v};{comment}");
            let at = doc
                .consts
                .values()
                .map(|c| c.line + 1)
                .max()
                .or_else(|| doc.first_line_starting("fn "))
                .unwrap_or(lines.len());
            let change = insert_line(&mut lines, at, text);
            ensure_blank_after(&mut lines, at + 1);
            change
        }
        NewParam::Switch { name } => {
            if !is_ident(name) {
                return Err("开关名要是标识符".into());
            }
            if doc.switches.contains_key(name) {
                return Err(format!("开关 {name} 已经存在"));
            }
            let at = lines.len();
            if at > 0 && !lines[at - 1].trim().is_empty() {
                lines.push(String::new());
            }
            let block = format!("set choose {name} {{\n}}\n\nchoose {name}");
            let start = lines.len();
            for l in block.lines() {
                lines.push(l.to_string());
            }
            Change {
                line: start + 1,
                before: None,
                after: Some(format!("set choose {name} {{ … }} / choose {name}")),
            }
        }
        NewParam::Capability { name, space, lo, hi, revocable } => {
            if !is_ident(name) || !is_ident(space) {
                return Err("能力域名和空间名都要是标识符".into());
            }
            if lo > hi {
                return Err("下界不能大于上界".into());
            }
            if doc.caps.contains_key(name) {
                return Err(format!("能力域 {name} 已经存在"));
            }
            let text = format!(
                "capability {name} : {space}[{lo}..{hi}]{}",
                if *revocable { " revocable" } else { "" }
            );
            let at = doc
                .caps
                .values()
                .map(|c| c.line + 1)
                .max()
                .or_else(|| doc.first_line_starting("fn "))
                .unwrap_or(lines.len());
            let change = insert_line(&mut lines, at, text);
            ensure_blank_after(&mut lines, at + 1);
            change
        }
        NewParam::Conf { key, ty, value } => {
            if !is_ident(key) {
                return Err("配置项名要是标识符".into());
            }
            if doc.conf.contains_key(key) {
                return Err(format!("配置项 {key} 已经存在"));
            }
            let v = normalize_const_value(ty, value)?;
            let text = format!("pub fn {key}() -> {ty} {{ return {v}; }}");
            let at = lines.len();
            insert_line(&mut lines, at, text)
        }
    };
    Ok(Applied { text: join(&lines, trailing), changes: vec![change] })
}

/* ---------------------------------------------------------------- explain */

pub fn explain(id: &str) -> Option<String> {
    if let Some(spec) = DIMS.iter().find(|d| d.dim == id) {
        let mut s = String::new();
        s.push_str(&format!("{}（{}）\n", spec.name, spec.dim));
        s.push_str(&format!("默认值：{}\n", spec.default));
        s.push_str(&format!("{}\n", spec.help));
        s.push_str("可选值：\n");
        for (v, note) in spec.values {
            s.push_str(&format!("  - {v}：{note}\n"));
        }
        return Some(s);
    }
    if let Some(name) = id.strip_prefix("switch:") {
        return Some(format!(
            "开关 {name}\n源码用 `set choose {name} {{ … }}` 定义。\n\
             打开（`choose {name}`）：那段代码参与编译。\n\
             关闭（`choose close {name}`）：那段代码完全不参与编译。\n\
             不写这一行等于关闭。"
        ));
    }
    if let Some(name) = id.strip_prefix("const:") {
        return Some(format!(
            "常量 {name}\n这是源码里的一个 `const`。改动只替换 `=` 和 `;` 之间的值，\
             类型、注释与其余排版都不动。改完用“校验”跑一遍 `lomentc --check`。"
        ));
    }
    if id.starts_with("cap:") {
        return Some(
            "能力域\n`capability 名字 : 空间[下界..上界] revocable` 定义一段可命中测试的授权区间。\n\
             下界/上界决定能碰哪些下标；`revocable` 决定这项能力能不能在运行期被收回。"
                .into(),
        );
    }
    if id.starts_with("conf:") {
        return Some(
            "项目配置\n`loment.conf` 里的一条声明，工具链按名字读它（例如 `source_ext` 决定\
             名字形式 `use` 找什么后缀）。改的是 `return` 后面那个值。"
                .into(),
        );
    }
    None
}

/// Windows canonical paths arrive as `\\?\D:\...`; strip the prefix for display.
pub fn display_path(p: &Path) -> String {
    let s = p.display().to_string();
    s.strip_prefix("\\\\?\\").unwrap_or(&s).to_string()
}

/// Safe file read: refuse paths outside the repo root.
pub fn read_in_root(root: &Path, rel: &str) -> Result<(std::path::PathBuf, String), String> {
    let p = Path::new(rel);
    let joined = if p.is_absolute() { p.to_path_buf() } else { root.join(p) };
    let canon = joined.canonicalize().map_err(|e| format!("找不到 {}: {e}", joined.display()))?;
    let rootc = root.canonicalize().map_err(|e| format!("仓库根不可用: {e}"))?;
    if !canon.starts_with(&rootc) {
        return Err("路径不在仓库里".into());
    }
    let text = std::fs::read_to_string(&canon).map_err(|e| format!("读不到 {}: {e}", canon.display()))?;
    Ok((canon, text))
}

#[cfg(test)]
mod tests {
    use super::*;

    const SRC: &str = r#"choose no_std
module demo

set choose verbose {
    let x: u32 = 1;
}

choose verbose

capability blk : disk[0..4] revocable

const MAX_FN: u32 = 4600;   // 函数表上限

fn main() {
    let y: u32 = 2;
}
"#;

    fn doc() -> Doc {
        parse("demo.lomt", SRC)
    }

    #[test]
    fn reads_dimensions_switches_and_consts() {
        let d = doc();
        assert_eq!(d.item("mode").unwrap().value, "no_std");
        assert_eq!(d.item("gc").unwrap().value, "gc_manual");
        assert_eq!(d.item("switch:verbose").unwrap().value, "on");
        assert_eq!(d.item("const:MAX_FN").unwrap().value, "4600");
        assert_eq!(d.item("const:MAX_FN").unwrap().help, "函数表上限");
        assert_eq!(d.item("cap:blk:lo").unwrap().value, "0");
        assert_eq!(d.item("cap:blk:hi").unwrap().value, "4");
        assert_eq!(d.item("cap:blk:revocable").unwrap().value, "true");
    }

    #[test]
    fn function_body_constants_are_not_parameters() {
        assert!(doc().item("const:y").is_none());
    }

    #[test]
    fn edits_are_surgical() {
        let d = doc();
        let out = apply(
            &d,
            &[
                Edit { id: "const:MAX_FN".into(), value: "5200".into() },
                Edit { id: "gc".into(), value: "gc_auto".into() },
                Edit { id: "cap:blk:hi".into(), value: "8".into() },
                Edit { id: "cap:blk:revocable".into(), value: "false".into() },
            ],
        )
        .unwrap();
        assert!(out.text.contains("const MAX_FN: u32 = 5200;   // 函数表上限"));
        assert!(out.text.contains("choose gc_auto"));
        assert!(out.text.contains("capability blk : disk[0..8]"));
        assert!(!out.text.contains("8] revocable"));
        assert!(out.text.contains("let y: u32 = 2;"));
        assert_eq!(out.changes.len(), 4);
    }

    #[test]
    fn inserting_a_dimension_goes_after_module() {
        let d = doc();
        let out = apply(&d, &[Edit { id: "runtime".into(), value: "runtime".into() }]).unwrap();
        let lines: Vec<&str> = out.text.lines().collect();
        let m = lines.iter().position(|l| l.starts_with("module")).unwrap();
        assert_eq!(lines[m + 1], "choose runtime");
    }

    #[test]
    fn grammar_goes_before_module() {
        let d = doc();
        let out = apply(&d, &[Edit { id: "grammar".into(), value: "python".into() }]).unwrap();
        let lines: Vec<&str> = out.text.lines().collect();
        let m = lines.iter().position(|l| l.starts_with("module")).unwrap();
        assert_eq!(lines[m - 1], "choose write grammar python");
    }

    #[test]
    fn toggling_a_switch_rewrites_the_value_line() {
        let d = doc();
        let out = apply(&d, &[Edit { id: "switch:verbose".into(), value: "off".into() }]).unwrap();
        assert!(out.text.contains("choose close verbose"));
    }

    #[test]
    fn bad_values_are_refused() {
        let d = doc();
        assert!(apply(&d, &[Edit { id: "gc".into(), value: "gc_nope".into() }]).is_err());
        assert!(apply(&d, &[Edit { id: "const:MAX_FN".into(), value: "abc".into() }]).is_err());
        assert!(apply(&d, &[Edit { id: "const:MAX_FN".into(), value: "1.5".into() }]).is_err());
        assert!(apply(&d, &[Edit { id: "cap:blk:lo".into(), value: "-1".into() }]).is_err());
    }

    #[test]
    fn reads_conf_files() {
        let text = "pub fn source_ext() -> str { return \".foo\"; }\n";
        let d = parse("loment.conf", text);
        assert_eq!(d.item("conf:source_ext").unwrap().value, "\".foo\"");
        let out = apply(&d, &[Edit { id: "conf:source_ext".into(), value: ".bar".into() }]).unwrap();
        assert!(out.text.contains("return \".bar\";"));
    }

    #[test]
    fn adds_each_kind() {
        let d = doc();
        let out = add(
            &d,
            &NewParam::Const { name: "LIMIT".into(), ty: "u32".into(), value: "9".into(), help: "上限".into() },
        )
        .unwrap();
        assert!(out.text.contains("const LIMIT: u32 = 9;   // 上限"));

        let out = add(&d, &NewParam::Switch { name: "trace".into() }).unwrap();
        assert!(out.text.contains("set choose trace {"));
        assert!(out.text.contains("choose trace"));

        let out = add(
            &d,
            &NewParam::Capability { name: "net".into(), space: "disk".into(), lo: 1, hi: 3, revocable: true },
        )
        .unwrap();
        assert!(out.text.contains("capability net : disk[1..3] revocable"));

        let out = add(
            &d,
            &NewParam::Conf { key: "source_ext".into(), ty: "str".into(), value: "".into() },
        )
        .unwrap();
        assert!(out.text.contains("pub fn source_ext() -> str"));
    }

    #[test]
    fn explain_covers_all_kinds() {
        for id in ["gc", "switch:verbose", "const:MAX_FN", "cap:blk:lo", "conf:source_ext"] {
            assert!(explain(id).is_some(), "{id}");
        }
    }

    #[test]
    fn catalog_overrides_help_and_group() {
        let cat = Catalog {
            consts: vec![catalog::Rule {
                pattern: "MAX_*".into(),
                help: Some("目录里的说明".into()),
                group: Some("容量".into()),
                unit: Some("条".into()),
                min: Some(1.0),
                max: Some(9000.0),
                note: Some("越界是段错误".into()),
            }],
            ..Default::default()
        };
        let d = parse_with("demo.lomt", SRC, &cat);
        let it = d.item("const:MAX_FN").unwrap();
        assert_eq!(it.help, "目录里的说明");
        assert_eq!(it.group, "容量");
        assert_eq!(it.unit.as_deref(), Some("条"));
        assert_eq!(it.max, Some(9000.0));
    }
}
