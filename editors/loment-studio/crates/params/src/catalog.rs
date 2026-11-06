//! A repo-editable description layer. New language features can add help text,
//! units, ranges and grouping here without touching Rust code.

use std::path::Path;

use serde::Deserialize;

#[derive(Clone, Debug, Default, Deserialize)]
pub struct Catalog {
    #[serde(default)]
    pub consts: Vec<Rule>,
    #[serde(default)]
    pub switches: Vec<Rule>,
    #[serde(default)]
    pub caps: Vec<Rule>,
    #[serde(default)]
    pub conf: Vec<Rule>,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct Rule {
    /// Exact name, or a prefix ending in `*` (`CG_*`).
    #[serde(default)]
    pub pattern: String,
    #[serde(default)]
    pub help: Option<String>,
    #[serde(default)]
    pub group: Option<String>,
    #[serde(default)]
    pub unit: Option<String>,
    #[serde(default)]
    pub min: Option<f64>,
    #[serde(default)]
    pub max: Option<f64>,
    /// A caution shown next to the control (e.g. "越界是段错误").
    #[serde(default)]
    pub note: Option<String>,
}

impl Catalog {
    pub fn load(path: &Path) -> Catalog {
        std::fs::read_to_string(path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_default()
    }

    fn pick<'a>(rules: &'a [Rule], name: &str) -> Option<&'a Rule> {
        rules
            .iter()
            .find(|r| r.pattern == name)
            .or_else(|| {
                rules.iter().find(|r| {
                    r.pattern
                        .strip_suffix('*')
                        .map(|p| name.starts_with(p))
                        .unwrap_or(false)
                })
            })
    }

    pub fn for_const(&self, name: &str) -> Option<&Rule> {
        Self::pick(&self.consts, name)
    }
    pub fn for_switch(&self, name: &str) -> Option<&Rule> {
        Self::pick(&self.switches, name)
    }
    pub fn for_cap(&self, name: &str) -> Option<&Rule> {
        Self::pick(&self.caps, name)
    }
    pub fn for_conf(&self, name: &str) -> Option<&Rule> {
        Self::pick(&self.conf, name)
    }
}
