//! Headless tileable texture synthesis (no Photoshop needed).
//! Headless texture synthesis: value-noise fBm on a wrapping
//! lattice, albedo / roughness / normal trio per preset. Deterministic per
//! (preset, seed); self-consistent across runs (own SplitMix64 stream, not
//! CPython's Mersenne Twister — byte equality with Python is not a goal).

use std::path::{Path, PathBuf};

use anyhow::Result;
use serde_json::Value;

pub const PRESETS: [&str; 13] = [
    "stone",
    "wood_grain",
    "noise",
    "bark",
    "mossy",
    "grass_blend",
    "worn_metal",
    "fabric",
    "plaster",
    "concrete",
    "brushed_metal",
    "leather",
    "soil",
];

struct Rng(u64);

impl Rng {
    fn next_f64(&mut self) -> f64 {
        // SplitMix64 -> [0,1)
        self.0 = self.0.wrapping_add(0x9E3779B97F4A7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
        let _ = z ^ (z >> 31);
        ((z >> 11) as f64) / ((1u64 << 53) as f64)
    }
}

fn lattice(n: usize, seed: u64) -> Vec<Vec<f64>> {
    let mut rng = Rng(seed);
    (0..n)
        .map(|_| (0..n).map(|_| rng.next_f64()).collect())
        .collect()
}

fn noise(latt: &[Vec<f64>], x: f64, y: f64) -> f64 {
    let n = latt.len() as f64;
    let ni = latt.len();
    let xi = x.floor() as i64;
    let yi = y.floor() as i64;
    let xf = x - x.floor();
    let yf = y - y.floor();
    let sx = xf * xf * (3.0 - 2.0 * xf);
    let sy = yf * yf * (3.0 - 2.0 * yf);
    let x0 = xi.rem_euclid(ni as i64) as usize;
    let x1 = (xi + 1).rem_euclid(ni as i64) as usize;
    let y0 = yi.rem_euclid(ni as i64) as usize;
    let y1 = (yi + 1).rem_euclid(ni as i64) as usize;
    let _ = n;
    let a = latt[y0][x0] + (latt[y0][x1] - latt[y0][x0]) * sx;
    let b = latt[y1][x0] + (latt[y1][x1] - latt[y1][x0]) * sx;
    a + (b - a) * sy
}

fn fbm(size: usize, base: usize, octaves: usize, seed: u64) -> Vec<Vec<f64>> {
    let latts: Vec<Vec<Vec<f64>>> = (0..octaves)
        .map(|o| lattice(base * (1 << o), seed.wrapping_add(o as u64 * 7919)))
        .collect();
    let mut out = vec![vec![0.0; size]; size];
    let mut amp = 1.0;
    let mut total = 0.0;
    for (o, latt) in latts.iter().enumerate() {
        let freq = (base * (1 << o)) as f64 / size as f64;
        for y in 0..size {
            for x in 0..size {
                out[y][x] += noise(latt, x as f64 * freq, y as f64 * freq) * amp;
            }
        }
        total += amp;
        amp *= 0.5;
    }
    for row in out.iter_mut() {
        for v in row.iter_mut() {
            *v /= total;
        }
    }
    out
}

fn albedo_range(preset: &str) -> ((u8, u8, u8), (u8, u8, u8)) {
    match preset {
        "stone" => ((88, 86, 84), (170, 167, 161)),
        "wood_grain" => ((101, 67, 33), (178, 134, 84)),
        "bark" => ((46, 30, 18), (122, 88, 58)),
        "mossy" => ((52, 74, 40), (112, 128, 84)),
        "grass_blend" => ((58, 96, 40), (128, 150, 84)),
        "worn_metal" => ((110, 92, 70), (168, 168, 172)),
        "fabric" => ((96, 96, 100), (178, 178, 184)),
        "plaster" => ((196, 193, 188), (238, 236, 231)),
        "concrete" => ((110, 110, 112), (178, 178, 180)),
        "brushed_metal" => ((120, 122, 126), (200, 202, 206)),
        "leather" => ((74, 48, 30), (150, 112, 78)),
        "soil" => ((46, 32, 20), (128, 98, 62)),
        _ => ((96, 96, 96), (168, 168, 168)),
    }
}

fn normal_strength(preset: &str) -> f64 {
    match preset {
        "stone" => 2.0,
        "wood_grain" => 1.2,
        "bark" => 3.0,
        "mossy" => 2.2,
        "grass_blend" => 0.6,
        "worn_metal" => 0.7,
        "fabric" => 1.6,
        "plaster" => 0.4,
        "concrete" => 1.4,
        "brushed_metal" => 0.5,
        "leather" => 1.8,
        "soil" => 2.4,
        _ => 0.8,
    }
}

fn base_freq(preset: &str) -> usize {
    match preset {
        "wood_grain" => 4,
        "mossy" => 4,
        "noise" => 8,
        "worn_metal" => 8,
        "bark" => 10,
        "leather" => 12,
        "fabric" => 24,
        "plaster" => 3,
        "concrete" => 5,
        "soil" => 5,
        "brushed_metal" => 6,
        "grass_blend" => 6,
        _ => 6,
    }
}

fn rough_range(preset: &str) -> (f64, f64) {
    match preset {
        "stone" => (0.6, 0.95),
        "wood_grain" => (0.45, 0.8),
        "bark" => (0.7, 1.0),
        "mossy" => (0.75, 1.0),
        "grass_blend" => (0.7, 1.0),
        "worn_metal" => (0.3, 0.7),
        "fabric" => (0.8, 1.0),
        "plaster" => (0.85, 1.0),
        "concrete" => (0.7, 0.95),
        "brushed_metal" => (0.25, 0.55),
        "leather" => (0.55, 0.85),
        "soil" => (0.9, 1.0),
        _ => (0.55, 1.0),
    }
}

fn fbm_aniso(size: usize, base: usize, octaves: usize, seed: u64, sx: f64, sy: f64) -> Vec<Vec<f64>> {
    let latts: Vec<Vec<Vec<f64>>> = (0..octaves)
        .map(|o| lattice(base * (1 << o), seed.wrapping_add(o as u64 * 7919)))
        .collect();
    let mut out = vec![vec![0.0; size]; size];
    let mut amp = 1.0;
    let mut total = 0.0;
    for (o, latt) in latts.iter().enumerate() {
        let fx = (base * (1 << o)) as f64 / size as f64 * sx;
        let fy = (base * (1 << o)) as f64 / size as f64 * sy;
        for y in 0..size {
            for x in 0..size {
                out[y][x] += noise(latt, x as f64 * fx, y as f64 * fy) * amp;
            }
        }
        total += amp;
        amp *= 0.5;
    }
    for row in out.iter_mut() {
        for v in row.iter_mut() {
            *v /= total;
        }
    }
    out
}

fn mix_height(a: &[Vec<f64>], b: &[Vec<f64>], amount: f64) -> Vec<Vec<f64>> {
    a.iter()
        .zip(b.iter())
        .map(|(ra, rb)| {
            ra.iter()
                .zip(rb.iter())
                .map(|(va, vb)| va + (vb - va) * amount)
                .collect()
        })
        .collect()
}

fn height(preset: &str, size: usize, seed: u64) -> Vec<Vec<f64>> {
    let base = base_freq(preset);
    let h = fbm(size, base, 4, seed);
    let fine = fbm(size, base * 4, 3, seed.wrapping_add(101));
    if preset == "wood_grain" {
        let warp = fbm(size, base, 3, seed.wrapping_add(101));
        const RINGS: f64 = 14.0;
        let mut out = vec![vec![0.0; size]; size];
        for y in 0..size {
            for x in 0..size {
                let u = x as f64 / size as f64 * RINGS + warp[y][x] * 3.0;
                let band = (u * std::f64::consts::PI).sin().abs();
                out[y][x] = (h[y][x] * 0.45 + band * 0.55).clamp(0.0, 1.0);
            }
        }
        return mix_height(&out, &fine, 0.22);
    }
    if preset == "fabric" {
        let mut out = vec![vec![0.0; size]; size];
        for y in 0..size {
            for x in 0..size {
                let wx = 0.5 + 0.5 * (x as f64 / size as f64 * base as f64 * std::f64::consts::PI * 2.0).sin();
                let wy = 0.5 + 0.5 * (y as f64 / size as f64 * base as f64 * std::f64::consts::PI * 2.0).sin();
                out[y][x] = (wx * wy * 0.7 + h[y][x] * 0.3).clamp(0.0, 1.0);
            }
        }
        return out;
    }
    if preset == "plaster" {
        // Keep the full 0..1 field so rough/normal maps carry detail; the
        // narrow albedo range is what reads as flat plaster, and the low
        // normal strength keeps the bump subtle.
        return mix_height(&h, &fine, 0.15);
    }
    if preset == "brushed_metal" {
        let streak = fbm_aniso(size, base, 3, seed.wrapping_add(202), 1.0, 8.0);
        return mix_height(&streak, &fine, 0.25);
    }
    if preset == "leather" {
        let cells = fbm(size, base, 2, seed.wrapping_add(303));
        let sharp: Vec<Vec<f64>> = cells
            .into_iter()
            .map(|row| row.into_iter().map(|v| ((v - 0.35) * 2.2).clamp(0.0, 1.0)).collect())
            .collect();
        return mix_height(&sharp, &fine, 0.4);
    }
    let amount = match preset {
        "bark" => 0.3,
        "mossy" => 0.4,
        "grass_blend" => 0.35,
        "worn_metal" => 0.3,
        "concrete" => 0.45,
        "soil" => 0.45,
        _ => 0.25,
    };
    mix_height(&h, &fine, amount)
}

fn write_png(path: &Path, rows: &[Vec<u8>], size: usize) -> Result<()> {
    let file = std::fs::File::create(path)?;
    let w = std::io::BufWriter::new(file);
    let mut enc = png::Encoder::new(w, size as u32, size as u32);
    enc.set_color(png::ColorType::Rgb);
    enc.set_depth(png::BitDepth::Eight);
    let mut writer = enc.write_header()?;
    let mut flat = Vec::with_capacity(size * size * 3);
    for row in rows {
        flat.extend_from_slice(row);
    }
    writer.write_image_data(&flat)?;
    Ok(())
}

pub fn make_texture(
    preset: &str,
    size: usize,
    seed: u64,
    out_dir: &Path,
    name: Option<&str>,
) -> Result<Value> {
    if !PRESETS.contains(&preset) {
        anyhow::bail!(
            "unknown texture preset {preset:?}; choose from {:?}",
            PRESETS
        );
    }
    let size = size.clamp(64, 1024);
    std::fs::create_dir_all(out_dir)?;
    let tag = match name {
        Some(n) => n.to_string(),
        None => format!("{preset}_{seed}"),
    };

    let h = height(preset, size, seed);
    let (lo, hi) = albedo_range(preset);
    let strength = normal_strength(preset);

    let mut albedo_rows: Vec<Vec<u8>> = Vec::with_capacity(size);
    let mut rough_rows: Vec<Vec<u8>> = Vec::with_capacity(size);
    let mut normal_rows: Vec<Vec<u8>> = Vec::with_capacity(size);
    for y in 0..size {
        let mut a_row = Vec::with_capacity(size * 3);
        let mut r_row = Vec::with_capacity(size * 3);
        let mut n_row = Vec::with_capacity(size * 3);
        for x in 0..size {
            let v = h[y][x];
            let lo_c = [lo.0 as f64, lo.1 as f64, lo.2 as f64];
            let hi_c = [hi.0 as f64, hi.1 as f64, hi.2 as f64];
            for i in 0..3 {
                a_row.push(
                    (lo_c[i] + (hi_c[i] - lo_c[i]) * v)
                        .round()
                        .clamp(0.0, 255.0) as u8,
                );
            }
            let (rlo, rhi) = rough_range(preset);
            let g = (255.0 * (rhi + (rlo - rhi) * v)).round() as u8;
            r_row.extend_from_slice(&[g, g, g]);
            let dx = (h[y][(x + 1) % size] - h[y][(x + size - 1) % size]) * strength;
            let dy = (h[(y + 1) % size][x] - h[(y + size - 1) % size][x]) * strength;
            let inv = 1.0 / (dx * dx + dy * dy + 1.0).sqrt();
            n_row.push(((-dx * inv * 0.5 + 0.5) * 255.0).round() as u8);
            n_row.push(((-dy * inv * 0.5 + 0.5) * 255.0).round() as u8);
            n_row.push(((inv * 0.5 + 0.5) * 255.0).round() as u8);
        }
        albedo_rows.push(a_row);
        rough_rows.push(r_row);
        normal_rows.push(n_row);
    }

    let mut files = serde_json::Map::new();
    for (kind, rows) in [
        ("albedo", &albedo_rows),
        ("rough", &rough_rows),
        ("normal", &normal_rows),
    ] {
        let p: PathBuf = out_dir.join(format!("{tag}_{kind}.png"));
        write_png(&p, rows, size)?;
        files.insert(
            kind.to_string(),
            Value::String(p.to_string_lossy().into_owned()),
        );
    }
    Ok(
        serde_json::json!({"preset": preset, "size": size, "seed": seed,
                          "tileable": true, "files": files}),
    )
}
