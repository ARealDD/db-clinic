use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::sync::Arc;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkillType {
    Case,
    Knowledge,
}

impl SkillType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Case => "case",
            Self::Knowledge => "knowledge",
        }
    }
}

#[derive(Debug, Clone)]
pub struct LoadedSkill {
    pub id: String,
    pub name: String,
    pub category: String,
    pub description: String,
    pub skill_type: SkillType,
    pub keywords: Vec<String>,
    pub symptoms: Vec<String>,
    pub triggers: Vec<String>,
    pub tags: Vec<String>,
    pub content: String,
}

#[derive(Debug, Clone)]
pub struct MatchedSkill {
    pub id: String,
    pub name: String,
    pub category: String,
    pub score: f32,
    pub skill_type: SkillType,
    /// Short summary copied from the skill's frontmatter `description:` —
    /// shown verbatim in the UI picker so the user can judge relevance
    /// without opening the full file.
    pub description: String,
}

pub struct SkillEngine {
    skills: Vec<LoadedSkill>,
}

static CATEGORY_HINTS: &[(&str, &[&str])] = &[
    (
        "slow_sql",
        &[
            "slow",
            "慢查询",
            "slow query",
            "timeout",
            "duration",
            "执行计划",
            "explain",
            "seq scan",
            "full table scan",
            "performance",
            "速度",
        ],
    ),
    (
        "lock",
        &[
            "lock",
            "deadlock",
            "死锁",
            "锁",
            "blocking",
            "blocked",
            "waiting",
            "idle in transaction",
            "lock wait",
        ],
    ),
    (
        "index",
        &[
            "index",
            "索引",
            "create index",
            "missing index",
            "seq scan",
            "no index",
        ],
    ),
    (
        "wait_events",
        &[
            "wait event",
            "lwlock",
            "io wait",
            "等待",
            "clientread",
            "wait_event",
        ],
    ),
    (
        "vacuum",
        &[
            "vacuum",
            "autovacuum",
            "bloat",
            "dead tuple",
            "dead rows",
            "膨胀",
        ],
    ),
];

fn normalize(text: &str) -> String {
    text.to_lowercase().replace(['_', '-'], " ")
}

fn split_frontmatter(text: &str) -> Option<(&str, &str)> {
    let text = text.trim_start_matches('\u{feff}');
    if !text.starts_with("---") {
        return None;
    }
    let rest = &text[3..];
    let end = rest.find("\n---")?;
    let frontmatter = rest[..end].trim();
    let body = rest[end + 4..].trim();
    Some((frontmatter, body))
}

fn parse_yaml_string(frontmatter: &str, key: &str) -> Option<String> {
    let prefix = format!("{key}:");
    for line in frontmatter.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix(&prefix) {
            let val = rest.trim().trim_matches('"').trim_matches('\'');
            if !val.is_empty() {
                return Some(val.to_string());
            }
        }
    }
    None
}

fn parse_yaml_list(frontmatter: &str, key: &str) -> Vec<String> {
    let mut result = Vec::new();
    let mut in_section = false;
    let header = format!("{key}:");
    for line in frontmatter.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with(&header) {
            in_section = true;
            continue;
        }
        if in_section {
            if let Some(item) = trimmed.strip_prefix("- ") {
                result.push(item.trim().trim_matches('"').trim_matches('\'').to_string());
            } else if !trimmed.is_empty() && !trimmed.starts_with('-') {
                break;
            }
        }
    }
    result
}

fn load_skill_from_file(path: &Path, skill_type: SkillType) -> Option<LoadedSkill> {
    let text = fs::read_to_string(path).ok()?;
    let (fm, body) = split_frontmatter(&text)?;

    let id = parse_yaml_string(fm, "id")?;
    let name = parse_yaml_string(fm, "name").unwrap_or_else(|| id.clone());
    let category = parse_yaml_string(fm, "category").unwrap_or_default();
    let description = parse_yaml_string(fm, "description").unwrap_or_default();
    let keywords = parse_yaml_list(fm, "keywords");
    let symptoms = parse_yaml_list(fm, "symptoms");
    let triggers = parse_yaml_list(fm, "triggers");
    let tags = parse_yaml_list(fm, "tags");

    Some(LoadedSkill {
        id,
        name,
        category,
        description,
        skill_type,
        keywords,
        symptoms,
        triggers,
        tags,
        content: body.to_string(),
    })
}

impl SkillEngine {
    pub fn load(skills_root: &Path) -> Arc<Self> {
        let mut skills = Vec::new();

        let case_dir = skills_root.join("case");
        if case_dir.is_dir() {
            Self::load_dir(&case_dir, SkillType::Case, &mut skills);
        }

        let knowledge_dir = skills_root.join("knowledge");
        if knowledge_dir.is_dir() {
            Self::load_dir(&knowledge_dir, SkillType::Knowledge, &mut skills);
        }

        eprintln!(
            "[SkillEngine] loaded {} skills ({} case, {} knowledge)",
            skills.len(),
            skills
                .iter()
                .filter(|s| s.skill_type == SkillType::Case)
                .count(),
            skills
                .iter()
                .filter(|s| s.skill_type == SkillType::Knowledge)
                .count(),
        );

        Arc::new(Self { skills })
    }

    fn load_dir(dir: &Path, skill_type: SkillType, out: &mut Vec<LoadedSkill>) {
        let Ok(entries) = fs::read_dir(dir) else {
            return;
        };
        for entry in entries {
            let Ok(entry) = entry else { continue };
            let path = entry.path();
            if path.is_dir() {
                let skill_md = path.join("SKILL.md");
                if skill_md.is_file() {
                    if let Some(skill) = load_skill_from_file(&skill_md, skill_type) {
                        out.push(skill);
                    } else {
                        eprintln!("[SkillEngine] skipped {}: parse failed", skill_md.display());
                    }
                }
            }
        }
    }

    pub fn match_skills(&self, user_message: &str, top_k: usize) -> Vec<MatchedSkill> {
        let context = normalize(user_message);
        let mut scored: Vec<MatchedSkill> = self
            .skills
            .iter()
            .filter_map(|skill| {
                let score = match skill.skill_type {
                    SkillType::Case => Self::score_case(skill, &context),
                    SkillType::Knowledge => Self::score_knowledge(skill, &context),
                };
                if score > 0.0 {
                    Some(MatchedSkill {
                        id: skill.id.clone(),
                        name: skill.name.clone(),
                        category: skill.category.clone(),
                        score,
                        skill_type: skill.skill_type,
                        description: skill.description.clone(),
                    })
                } else {
                    None
                }
            })
            .collect();

        scored.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        scored.truncate(top_k);
        scored
    }

    /// Resolve a caller-supplied list of skill ids to `MatchedSkill` records.
    /// Used by the user-driven picker path: the UI sends back which skills
    /// the operator chose, and we trust that choice (score := 1.0). Unknown
    /// ids are silently dropped. Output preserves the caller's order so
    /// `build_context` reflects the operator's priority.
    pub fn select_by_ids(&self, ids: &[&str]) -> Vec<MatchedSkill> {
        ids.iter()
            .filter_map(|id| self.skills.iter().find(|s| s.id == *id))
            .map(|skill| MatchedSkill {
                id: skill.id.clone(),
                name: skill.name.clone(),
                category: skill.category.clone(),
                score: 1.0,
                skill_type: skill.skill_type,
                description: skill.description.clone(),
            })
            .collect()
    }

    fn score_case(skill: &LoadedSkill, context: &str) -> f32 {
        let mut score: f32 = 0.0;

        for kw in &skill.keywords {
            if context.contains(&normalize(kw)) {
                score += 2.0;
            }
        }

        for symptom in &skill.symptoms {
            let norm = normalize(symptom);
            let words: Vec<&str> = norm.split_whitespace().filter(|w| w.len() >= 3).collect();
            let hits = words.iter().filter(|w| context.contains(*w)).count();
            if hits >= 2 {
                score += 1.5;
            }
        }

        for trigger in &skill.triggers {
            if let Ok(re) = regex::Regex::new(&format!("(?i){trigger}")) {
                if re.is_match(context) {
                    score += 3.0;
                }
            }
        }

        let hints = CATEGORY_HINTS
            .iter()
            .find(|(cat, _)| *cat == skill.category);
        if let Some((_, hint_words)) = hints {
            if hint_words.iter().any(|h| context.contains(h)) {
                score += 1.0;
            }
        }

        score
    }

    fn score_knowledge(skill: &LoadedSkill, context: &str) -> f32 {
        let mut score: f32 = 0.0;
        for kw in &skill.keywords {
            if context.contains(&normalize(kw)) {
                score += 2.0;
            }
        }
        for tag in &skill.tags {
            if context.contains(&normalize(tag)) {
                score += 0.5;
            }
        }
        score
    }

    pub fn build_context(&self, matched: &[MatchedSkill]) -> String {
        if matched.is_empty() {
            return String::new();
        }

        let skill_map: HashMap<&str, &LoadedSkill> =
            self.skills.iter().map(|s| (s.id.as_str(), s)).collect();

        let mut lines = vec!["[Matched Diagnostic Skills]".to_string(), String::new()];

        for m in matched {
            if let Some(skill) = skill_map.get(m.id.as_str()) {
                match skill.skill_type {
                    SkillType::Case => {
                        lines.push(format!("## Skill: {}", skill.name));
                        lines.push(format!("Category: {}", skill.category));
                        if !skill.description.is_empty() {
                            lines.push(format!("Description: {}", skill.description));
                        }
                        lines.push(String::new());
                        lines.push(skill.content.clone());
                        lines.push(String::new());
                    }
                    SkillType::Knowledge => {
                        lines.push(format!("### Knowledge: {}", skill.name));
                        if !skill.description.is_empty() {
                            lines.push(format!("*{}*", skill.description));
                        }
                        lines.push(String::new());
                        lines.push(skill.content.clone());
                        lines.push(String::new());
                    }
                }
            }
        }

        lines.push("[End of Matched Skills]".to_string());
        lines.join("\n")
    }
}
