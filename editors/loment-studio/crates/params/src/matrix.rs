//! The environment matrix: every legal combination of the language dimensions,
//! with the conflicts the compiler itself refuses.

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Axis {
    pub dim: String,
    pub label: String,
    pub values: Vec<String>,
    #[serde(default)]
    pub notes: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Cell {
    /// (dim, value) pairs in the same order as the selected axes.
    pub combo: Vec<(String, String)>,
    pub valid: bool,
    pub reason: Option<String>,
}

pub fn axes() -> Vec<Axis> {
    crate::DIMS
        .iter()
        .map(|d| Axis {
            dim: d.dim.into(),
            label: d.name.into(),
            values: d.values.iter().map(|(v, _)| (*v).to_string()).collect(),
            notes: d.values.iter().map(|(_, n)| (*n).to_string()).collect(),
        })
        .collect()
}

/// `CORE_CONFLICTS` from `tools/lomentc.py`, mirrored so the matrix can explain
/// a cell instead of just greying it out.
fn conflict(values: &[(&str, &str)]) -> Option<&'static str> {
    let has = |a: &str, b: &str| {
        values.iter().any(|(_, v)| *v == a) && values.iter().any(|(_, v)| *v == b)
    };
    if has("no_std", "gc_auto") {
        return Some("no_std 不许自动回收：核要保持小，收集器没有地方住");
    }
    if has("no_std", "gc_auto_alpha") {
        return Some("no_std 不许混合档：它比 gc_auto 更依赖运行期");
    }
    if has("gc_auto", "no_runtime") {
        return Some("自动回收要运行期，no_runtime 说产物里不要它");
    }
    if has("gc_auto_alpha", "no_runtime") {
        return Some("混合档要策略池与收集点，都在运行期里");
    }
    None
}

pub fn build(selected: &[String]) -> Vec<Cell> {
    let all = axes();
    let chosen: Vec<Axis> = selected
        .iter()
        .filter_map(|d| all.iter().find(|a| &a.dim == d).cloned())
        .collect();
    if chosen.is_empty() {
        return Vec::new();
    }
    let mut cells = Vec::new();
    let mut idx = vec![0usize; chosen.len()];
    loop {
        let combo: Vec<(String, String)> = chosen
            .iter()
            .zip(idx.iter())
            .map(|(a, i)| (a.dim.clone(), a.values[*i].clone()))
            .collect();
        let pairs: Vec<(&str, &str)> = combo
            .iter()
            .map(|(d, v)| (d.as_str(), v.as_str()))
            .collect();
        let reason = conflict(&pairs);
        cells.push(Cell {
            combo,
            valid: reason.is_none(),
            reason: reason.map(str::to_string),
        });
        // odometer
        let mut k = chosen.len();
        loop {
            if k == 0 {
                return cells;
            }
            k -= 1;
            idx[k] += 1;
            if idx[k] < chosen[k].values.len() {
                break;
            }
            idx[k] = 0;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn three_axes_give_twelve_cells() {
        let cells = build(&["mode".into(), "gc".into(), "runtime".into()]);
        assert_eq!(cells.len(), 12);
        // gc_manual: 2 modes × 2 runtime; each auto tier: only std + runtime.
        assert_eq!(cells.iter().filter(|c| c.valid).count(), 6);
    }

    #[test]
    fn conflicts_name_the_reason() {
        let cells = build(&["mode".into(), "gc".into(), "runtime".into()]);
        let bad = cells
            .iter()
            .find(|c| !c.valid)
            .expect("至少一个冲突");
        assert!(bad.reason.as_deref().unwrap_or("").contains("运行期"));
    }
}
