//! Local backend for the parameter desk. The browser never parses Loment:
//! it asks this server for the catalog, a patch preview, or a `lomentc` check.

use std::path::{Path, PathBuf};
use std::process::Command;

use loment_params::{add, apply, matrix, parse_with, read_in_root, Catalog, Edit, NewParam};
use serde::Deserialize;
use serde_json::json;
use tiny_http::{Header, Response, Server, StatusCode};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut port: u16 = 8710;
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--port" && i + 1 < args.len() {
            port = args[i + 1].parse().unwrap_or(8710);
            i += 2;
        } else {
            i += 1;
        }
    }
    let Some(root) = repo_root() else {
        eprintln!("找不到 tools/lomentc.py —— 请在 Loment 仓库里运行");
        std::process::exit(1);
    };
    let web = web_dir(&root);

    let server = Server::http(("127.0.0.1", port)).expect("bind 127.0.0.1");
    println!("Loment Studio: http://127.0.0.1:{port}");
    for mut request in server.incoming_requests() {
        let url = request.url().to_string();
        let (path, query) = split_url(&url);
        let result = match (request.method().as_str(), path.as_str()) {
            ("GET", "/api/params") => match param_doc(&root, query.get("path")) {
                Ok((file, doc)) => json_response(
                    &json!({
                        "path": loment_params::display_path(&file),
                        "rel": query.get("path"),
                        "lines": doc.line_count(),
                        "items": doc.items,
                    })
                    .to_string(),
                ),
                Err(e) => error(400, &e),
            },
            ("GET", "/api/matrix") => match param_doc(&root, query.get("path")) {
                Ok((_, doc)) => {
                    let selected: Vec<String> = query
                        .get("axes")
                        .map(|v| v.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect())
                        .unwrap_or_else(|| vec!["mode".into(), "gc".into(), "runtime".into()]);
                    let cells = matrix::build(&selected);
                    let current: Vec<serde_json::Value> = selected
                        .iter()
                        .map(|d| {
                            json!({
                                "dim": d,
                                "value": doc.item(d).map(|i| i.value.clone()).unwrap_or_default(),
                            })
                        })
                        .collect();
                    json_response(
                        &json!({
                            "axes": matrix::axes(),
                            "selected": selected,
                            "cells": cells,
                            "current": current,
                        })
                        .to_string(),
                    )
                }
                Err(e) => error(400, &e),
            },
            ("GET", "/api/source") => match query.get("path") {
                Some(p) => match read_in_root(&root, p) {
                    Ok((_, text)) => json_response(&json!({ "text": text }).to_string()),
                    Err(e) => error(404, &e),
                },
                None => error(400, "缺少 path"),
            },
            ("POST", "/api/preview") => match read_json::<PatchReq>(&mut request) {
                Ok(req) => match param_doc(&root, Some(&req.path)) {
                    Ok((_, doc)) => match apply(&doc, &req.edits) {
                        Ok(out) => json_response(
                            &json!({ "text": out.text, "changes": out.changes }).to_string(),
                        ),
                        Err(e) => error(400, &e),
                    },
                    Err(e) => error(400, &e),
                },
                Err(e) => error(400, &e),
            },
            ("POST", "/api/apply") => match read_json::<PatchReq>(&mut request) {
                Ok(req) => match param_doc(&root, Some(&req.path)) {
                    Ok((file, doc)) => match apply(&doc, &req.edits) {
                        Ok(out) => match std::fs::write(&file, &out.text) {
                            Ok(()) => json_response(
                                &json!({ "changes": out.changes, "written": loment_params::display_path(&file) })
                                    .to_string(),
                            ),
                            Err(e) => error(500, &format!("写回失败: {e}")),
                        },
                        Err(e) => error(400, &e),
                    },
                    Err(e) => error(400, &e),
                },
                Err(e) => error(400, &e),
            },
            ("POST", "/api/add") => match read_json::<AddReq>(&mut request) {
                Ok(req) => match param_doc(&root, Some(&req.path)) {
                    Ok((file, doc)) => match add(&doc, &req.param) {
                        Ok(out) => {
                            if req.dry {
                                json_response(&json!({ "changes": out.changes }).to_string())
                            } else {
                                match std::fs::write(&file, &out.text) {
                                    Ok(()) => json_response(
                                        &json!({
                                            "changes": out.changes,
                                            "written": loment_params::display_path(&file),
                                        })
                                        .to_string(),
                                    ),
                                    Err(e) => error(500, &format!("写回失败: {e}")),
                                }
                            }
                        }
                        Err(e) => error(400, &e),
                    },
                    Err(e) => error(400, &e),
                },
                Err(e) => error(400, &e),
            },
            ("POST", "/api/check") => match read_json::<CheckReq>(&mut request) {
                Ok(req) => match param_doc(&root, Some(&req.path)) {
                    Ok((file, _)) => match check(&root, &file) {
                        Ok(diags) => json_response(&json!({ "diags": diags }).to_string()),
                        Err(e) => error(500, &e),
                    },
                    Err(e) => error(400, &e),
                },
                Err(e) => error(400, &e),
            },
            ("GET", _) => match static_file(&web, &path) {
                Some((body, mime)) => Ok((StatusCode(200), mime.into_bytes(), body)),
                None => error(404, "not found"),
            },
            _ => error(405, "method not allowed"),
        };
        let (status, ct, body) = match result {
            Ok(v) => v,
            Err((code, msg)) => (
                StatusCode(code),
                b"text/plain; charset=utf-8".to_vec(),
                msg.into_bytes(),
            ),
        };
        let header = Header::from_bytes(&b"Content-Type"[..], &ct[..]).unwrap();
        let cache = Header::from_bytes(&b"Cache-Control"[..], &b"no-store"[..]).unwrap();
        let _ = request
            .respond(Response::from_data(body).with_status_code(status).with_header(header).with_header(cache));
    }
}

#[derive(Deserialize)]
struct PatchReq {
    path: String,
    #[serde(default)]
    edits: Vec<Edit>,
}

#[derive(Deserialize)]
struct CheckReq {
    path: String,
}

#[derive(Deserialize)]
struct AddReq {
    path: String,
    param: NewParam,
    #[serde(default)]
    dry: bool,
}

type Reply = Result<(StatusCode, Vec<u8>, Vec<u8>), (u16, String)>;

fn json_response(body: &str) -> Reply {
    Ok((StatusCode(200), b"application/json; charset=utf-8".to_vec(), body.as_bytes().to_vec()))
}

fn error(code: u16, msg: &str) -> Reply {
    Err((code, msg.to_string()))
}

fn read_json<T: for<'de> Deserialize<'de>>(request: &mut tiny_http::Request) -> Result<T, String> {
    let mut s = String::new();
    request
        .as_reader()
        .read_to_string(&mut s)
        .map_err(|e| format!("读请求失败: {e}"))?;
    serde_json::from_str(&s).map_err(|e| format!("请求不是合法 JSON: {e}"))
}

fn param_doc(root: &Path, rel: Option<&String>) -> Result<(PathBuf, loment_params::Doc), String> {
    let rel = rel.ok_or("缺少 path")?;
    let cat = Catalog::load(&root.join("editors/loment-studio/param-catalog.json"));
    let (file, text) = read_in_root(root, rel)?;
    Ok((file.clone(), parse_with(loment_params::display_path(&file), &text, &cat)))
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

fn web_dir(root: &Path) -> PathBuf {
    root.join("editors").join("loment-studio").join("web")
}

fn split_url(url: &str) -> (String, std::collections::HashMap<String, String>) {
    let (path, qs) = match url.split_once('?') {
        Some((p, q)) => (p.to_string(), q),
        None => (url.to_string(), ""),
    };
    let mut map = std::collections::HashMap::new();
    for pair in qs.split('&') {
        if let Some((k, v)) = pair.split_once('=') {
            map.insert(decode(k), decode(v));
        }
    }
    (path, map)
}

fn decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or("");
                match u8::from_str_radix(hex, 16) {
                    Ok(b) => {
                        out.push(b);
                        i += 3;
                    }
                    Err(_) => {
                        out.push(bytes[i]);
                        i += 1;
                    }
                }
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn static_file(web: &Path, path: &str) -> Option<(Vec<u8>, String)> {
    let rel = if path == "/" { "index.html" } else { path.trim_start_matches('/') };
    if rel.contains("..") {
        return None;
    }
    let file = web.join(rel);
    let body = std::fs::read(&file).ok()?;
    let mime = match file.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "js" => "text/javascript; charset=utf-8",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        _ => "application/octet-stream",
    };
    Some((body, mime.to_string()))
}

fn python(root: &Path, args: &[String]) -> Result<std::process::Output, String> {
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

fn check(root: &Path, file: &Path) -> Result<Vec<serde_json::Value>, String> {
    let dir = std::env::temp_dir();
    let pid = std::process::id();
    let diag = dir.join(format!("loment-studio-{pid}.diag.json"));
    let _ = std::fs::remove_file(&diag);
    let out = python(
        root,
        &[
            "tools/lomentc.py".into(),
            "--check".into(),
            "--diag-out".into(),
            diag.to_string_lossy().into_owned(),
            file.to_string_lossy().into_owned(),
        ],
    )?;
    let mut v = Vec::new();
    if let Ok(text) = std::fs::read_to_string(&diag) {
        for line in text.lines() {
            if let Ok(d) = serde_json::from_str::<serde_json::Value>(line) {
                v.push(d);
            }
        }
    }
    if !out.status.success() && v.is_empty() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    Ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_percent_and_plus() {
        assert_eq!(decode("a%20b+c"), "a b c");
        assert_eq!(decode("loment%2Fselfhost"), "loment/selfhost");
    }

    #[test]
    fn splits_path_and_query() {
        let (p, q) = split_url("/api/params?path=loment%2Fselfhost%2Fcodegen.lomt");
        assert_eq!(p, "/api/params");
        assert_eq!(q.get("path").map(String::as_str), Some("loment/selfhost/codegen.lomt"));
    }

    #[test]
    fn static_lookup_refuses_traversal() {
        let web = std::env::temp_dir();
        assert!(static_file(&web, "/../etc/passwd").is_none());
    }
}
