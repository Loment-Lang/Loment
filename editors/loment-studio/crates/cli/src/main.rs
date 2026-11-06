//! `loment-studio` — parameter desk for one `.lomt` file.
//!
//!   loment-studio list    <file> [--json]
//!   loment-studio explain <id>
//!   loment-studio set     <file> <id> <value> [--dry-run]
//!   loment-studio check   <file>

use std::path::{Path, PathBuf};
use std::process::Command;

use loment_params::{
    add, apply, explain, matrix, parse_with, read_in_root, Catalog, Edit, ItemKind, NewParam,
};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match run(&args) {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("{e}");
            1
        }
    };
    std::process::exit(code);
}

fn run(args: &[String]) -> Result<(), String> {
    let Some(cmd) = args.first().map(String::as_str) else {
        return Err(usage());
    };
    match cmd {
        "list" => {
            let file = args.get(1).ok_or_else(usage)?;
            let json = args.iter().any(|a| a == "--json");
            let doc = load(file)?;
            if json {
                println!(
                    "{}",
                    serde_json::json!({
                        "path": doc.path,
                        "lines": doc.line_count(),
                        "items": doc.items,
                    })
                );
            } else {
                print_list(&doc);
            }
            Ok(())
        }
        "explain" => {
            let id = args.get(1).ok_or_else(usage)?;
            match explain(id) {
                Some(text) => {
                    println!("{text}");
                    Ok(())
                }
                None => Err(format!("没有这个参数: {id}（试试 `list <file>`）")),
            }
        }
        "set" => {
            let file = args.get(1).ok_or_else(usage)?;
            let id = args.get(2).ok_or_else(usage)?;
            let value = args.get(3).ok_or_else(usage)?;
            let dry = args.iter().any(|a| a == "--dry-run");
            let doc = load_with_path(file)?;
            let out = apply(
                &doc.doc,
                &[Edit { id: id.clone(), value: value.clone() }],
            )?;
            if out.changes.is_empty() {
                println!("没有变化（{id} 已经是 {value}）");
                return Ok(());
            }
            print_changes(&out.changes);
            if dry {
                println!("（--dry-run：没有写回）");
            } else {
                std::fs::write(&doc.path, out.text)
                    .map_err(|e| format!("写回 {} 失败: {e}", doc.path.display()))?;
                println!("已写回 {}", loment_params::display_path(&doc.path));
            }
            Ok(())
        }
        "matrix" => {
            let file = args.get(1).ok_or_else(usage)?;
            let json = args.iter().any(|a| a == "--json");
            let axes = arg_value(args, "--axes")
                .map(|v| v.split(',').map(|s| s.trim().to_string()).collect::<Vec<_>>())
                .unwrap_or_else(|| vec!["mode".into(), "gc".into(), "runtime".into()]);
            let loaded = load_with_path(file)?;
            let current: Vec<String> = axes
                .iter()
                .map(|d| loaded.doc.item(d).map(|i| i.value.clone()).unwrap_or_default())
                .collect();
            let cells = matrix::build(&axes);
            if json {
                println!("{}", serde_json::json!({ "axes": matrix::axes(), "cells": cells }));
            } else {
                let mut ok = 0;
                for c in &cells {
                    let combo = c
                        .combo
                        .iter()
                        .map(|(d, v)| format!("{d}={v}"))
                        .collect::<Vec<_>>()
                        .join("  ");
                    let here = c
                        .combo
                        .iter()
                        .zip(&current)
                        .all(|((_, v), cur)| v == cur);
                    if c.valid {
                        ok += 1;
                        println!("  ✓ {combo}{}", if here { "   ← 当前" } else { "" });
                    } else {
                        println!("  ✗ {combo}
      {}", c.reason.clone().unwrap_or_default());
                    }
                }
                println!();
                println!("{ok}/{} 组合合法", cells.len());
            }
            Ok(())
        }
        "add" => {
            let file = args.get(1).ok_or_else(usage)?;
            let kind = args.get(2).ok_or_else(usage)?;
            let dry = args.iter().any(|a| a == "--dry-run");
            let doc = load_with_path(file)?;
            let p = match kind.as_str() {
                "const" => NewParam::Const {
                    name: args.get(3).ok_or_else(usage)?.clone(),
                    ty: args.get(4).ok_or_else(usage)?.clone(),
                    value: args.get(5).ok_or_else(usage)?.clone(),
                    help: arg_value(args, "--help").unwrap_or_default(),
                },
                "switch" => NewParam::Switch { name: args.get(3).ok_or_else(usage)?.clone() },
                "capability" => NewParam::Capability {
                    name: args.get(3).ok_or_else(usage)?.clone(),
                    space: args.get(4).ok_or_else(usage)?.clone(),
                    lo: args.get(5).and_then(|v| v.parse().ok()).ok_or("下界要是整数")?,
                    hi: args.get(6).and_then(|v| v.parse().ok()).ok_or("上界要是整数")?,
                    revocable: args.iter().any(|a| a == "--revocable"),
                },
                "conf" => NewParam::Conf {
                    key: args.get(3).ok_or_else(usage)?.clone(),
                    ty: args.get(4).cloned().unwrap_or_else(|| "str".into()),
                    value: args.get(5).cloned().unwrap_or_default(),
                },
                other => return Err(format!("未知参数种类: {other}")),
            };
            let out = add(&doc.doc, &p)?;
            print_changes(&out.changes);
            if dry {
                println!("（--dry-run：没有写回）");
            } else {
                std::fs::write(&doc.path, out.text)
                    .map_err(|e| format!("写回 {} 失败: {e}", doc.path.display()))?;
                println!("已写回 {}", loment_params::display_path(&doc.path));
            }
            Ok(())
        }
        "check" => {
            let file = args.get(1).ok_or_else(usage)?;
            let root = repo_root().ok_or("找不到 tools/lomentc.py（请在 Loment 仓库里运行）")?;
            let (path, _) = read_in_root(&root, file)?;
            let out = python(
                &root,
                &["tools/lomentc.py", "--check", &path.to_string_lossy()],
            )?;
            print!("{}", String::from_utf8_lossy(&out.stdout));
            eprint!("{}", String::from_utf8_lossy(&out.stderr));
            if out.status.success() {
                Ok(())
            } else {
                Err("lomentc --check 未通过".into())
            }
        }
        _ => Err(usage()),
    }
}

struct Loaded {
    doc: loment_params::Doc,
    path: PathBuf,
}

fn load(file: &str) -> Result<loment_params::Doc, String> {
    Ok(load_with_path(file)?.doc)
}

fn load_with_path(file: &str) -> Result<Loaded, String> {
    let root = repo_root().unwrap_or_else(|| PathBuf::from("."));
    let cat = Catalog::load(&root.join("editors/loment-studio/param-catalog.json"));
    let (path, text) = read_in_root(&root, file)?;
    let doc = parse_with(loment_params::display_path(&path), &text, &cat);
    Ok(Loaded { doc, path })
}

fn print_list(doc: &loment_params::Doc) {
    println!("{}  ({} 行)", doc.path, doc.line_count());
    let mut seen: Vec<String> = Vec::new();
    for item in &doc.items {
        if !seen.contains(&item.group) {
            seen.push(item.group.clone());
        }
    }
    for group in seen {
        let items: Vec<_> = doc.items.iter().filter(|i| i.group == group).collect();
        if items.is_empty() {
            continue;
        }
        println!();
        println!("== {group} ({}) ===", items.len());
        for item in items {
            let mark = match item.kind {
                ItemKind::Choice if !item.written => "  (默认值，源码未写)",
                _ => "",
            };
            println!("  {:<30} = {}{}", item.id, item.value, mark);
            for line in wrap(&item.help, 86) {
                println!("      {line}");
            }
            if !item.options.is_empty() {
                println!("      可选: {}", item.options.join(" | "));
            }
            if item.line > 0 {
                println!("      源码: 第 {} 行 · 类型 {}", item.line, if item.ty.is_empty() { "—" } else { &item.ty });
            }
        }
    }
    println!();
    println!("用法: loment-studio set <file> <id> <value> [--dry-run]");
    println!("      loment-studio explain <id>");
}

fn arg_value(args: &[String], flag: &str) -> Option<String> {
    args.iter().position(|a| a == flag).and_then(|i| args.get(i + 1)).cloned()
}

fn print_changes(changes: &[loment_params::Change]) {
    for c in changes {
        match (&c.before, &c.after) {
            (Some(b), Some(a)) => {
                println!("第 {} 行", c.line);
                println!("  - {b}");
                println!("  + {a}");
            }
            (None, Some(a)) => {
                println!("第 {} 行（新增）", c.line);
                println!("  + {a}");
            }
            _ => {}
        }
    }
}

fn wrap(text: &str, width: usize) -> Vec<String> {
    let mut out = Vec::new();
    let mut line = String::new();
    for ch in text.chars() {
        if ch == '\n' {
            out.push(std::mem::take(&mut line));
            continue;
        }
        if line.chars().count() >= width {
            out.push(std::mem::take(&mut line));
        }
        line.push(ch);
    }
    if !line.is_empty() {
        out.push(line);
    }
    if out.is_empty() {
        out.push(String::new());
    }
    out
}

fn repo_root() -> Option<PathBuf> {
    if let Ok(cwd) = std::env::current_dir() {
        if let Some(r) = walk_up(cwd) {
            return Some(r);
        }
    }
    let exe = std::env::current_exe().ok()?;
    walk_up(exe.parent()?.to_path_buf())
}

fn walk_up(mut dir: PathBuf) -> Option<PathBuf> {
    loop {
        if dir.join("tools").join("lomentc.py").is_file() {
            return Some(dir);
        }
        if !dir.pop() {
            return None;
        }
    }
}

fn python(root: &Path, args: &[&str]) -> Result<std::process::Output, String> {
    let mut last = None;
    for exe in ["python", "python3"] {
        match Command::new(exe)
            .args(args)
            .current_dir(root)
            .env("PYTHONUTF8", "1")
            .output()
        {
            Ok(o) => return Ok(o),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => last = Some(format!("{exe}: {e}")),
            Err(e) => return Err(format!("启动 {exe} 失败: {e}")),
        }
    }
    Err(format!("找不到 python / python3 ({})", last.unwrap_or_default()))
}

fn usage() -> String {
    "用法:
  loment-studio list    <file> [--json]
  loment-studio explain <id>
  loment-studio set     <file> <id> <value> [--dry-run]
  loment-studio add     <file> const|switch|capability|conf ...
  loment-studio matrix  <file> [--axes mode,gc,runtime] [--json]
  loment-studio check   <file>

例:
  loment-studio list loment/selfhost/codegen.lomt
  loment-studio set loment/selfhost/codegen.lomt const:CG_MAXFN 5200 --dry-run
  loment-studio set loment/selfhost/codegen.lomt gc gc_auto
  loment-studio matrix loment/selfhost/codegen.lomt
  loment-studio add loment/selfhost/codegen.lomt const LIMIT u32 64 --help \"上限\" --dry-run"
        .into()
}
