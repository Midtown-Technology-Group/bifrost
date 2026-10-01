//! Reuse locked shortest digits; change decimal presentation only.

use super::EvidenceEncodeError;
use serde_json::ser::{CompactFormatter, Formatter};
use std::io::{self, Write};

struct Scratch {
    bytes: [u8; 32],
    len: usize,
}
impl Write for Scratch {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let end = self
            .len
            .checked_add(bytes.len())
            .filter(|end| *end <= self.bytes.len())
            .ok_or_else(|| io::Error::other("synthetic float scratch bound"))?;
        self.bytes[self.len..end].copy_from_slice(bytes);
        self.len = end;
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

pub(super) fn finite(value: f64) -> Result<String, EvidenceEncodeError> {
    let invalid = EvidenceEncodeError::FormatterInvariant;
    if !value.is_finite() {
        return Err(EvidenceEncodeError::NonFinite);
    }
    // Only bit classification decides the zero sign; numeric equality cannot erase it.
    if value.to_bits() & 0x7fff_ffff_ffff_ffff == 0 {
        return Ok(if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .to_owned());
    }
    let mut scratch = Scratch {
        bytes: [0; 32],
        len: 0,
    };
    CompactFormatter
        .write_f64(&mut scratch, value)
        .map_err(|_| invalid)?;
    let raw = std::str::from_utf8(&scratch.bytes[..scratch.len]).map_err(|_| invalid)?;
    let negative = raw.starts_with('-');
    if negative != value.is_sign_negative() {
        return Err(invalid);
    }
    let unsigned = raw.strip_prefix('-').unwrap_or(raw);
    let mut pieces = unsigned.split('e');
    let significand = pieces.next().ok_or(invalid)?;
    let exponent = match pieces.next() {
        Some(text) => {
            let digits = text.strip_prefix(['+', '-']).unwrap_or(text);
            if digits.is_empty() || digits.len() > 3 || !digits.bytes().all(|c| c.is_ascii_digit())
            {
                return Err(invalid);
            }
            text.parse::<i32>().map_err(|_| invalid)?
        }
        None => 0,
    };
    if pieces.next().is_some() {
        return Err(invalid);
    }
    let mut coefficient = String::new();
    let mut fractional_digits = 0_i32;
    let mut dot = false;
    for byte in significand.bytes() {
        match byte {
            b'.' if !dot => {
                dot = true;
            }
            b'0'..=b'9' => {
                coefficient.push(char::from(byte));
                if dot {
                    fractional_digits += 1;
                }
            }
            _ => return Err(invalid),
        }
    }
    if coefficient.is_empty() || significand.starts_with('.') || significand.ends_with('.') {
        return Err(invalid);
    }
    let mut coefficient = coefficient.trim_start_matches('0').to_owned();
    if coefficient.is_empty() {
        return Err(invalid);
    }
    let mut power = exponent.checked_sub(fractional_digits).ok_or(invalid)?;
    while coefficient.ends_with('0') {
        coefficient.pop();
        power = power.checked_add(1).ok_or(invalid)?;
    }
    if coefficient.is_empty() || coefficient.len() > 17 {
        return Err(invalid);
    }
    let digits = i32::try_from(coefficient.len()).map_err(|_| invalid)?;
    let scientific = power.checked_add(digits - 1).ok_or(invalid)?;
    if !(-324..=308).contains(&scientific) {
        return Err(invalid);
    }
    let mut output = String::with_capacity(24);
    if negative {
        output.push('-');
    }
    if (-4..=15).contains(&scientific) {
        let point = scientific + 1;
        if point <= 0 {
            output.push_str("0.");
            for _ in 0..-point {
                output.push('0');
            }
            output.push_str(&coefficient);
        } else if point >= digits {
            output.push_str(&coefficient);
            for _ in 0..point - digits {
                output.push('0');
            }
            output.push_str(".0");
        } else {
            let point = usize::try_from(point).map_err(|_| invalid)?;
            output.push_str(&coefficient[..point]);
            output.push('.');
            output.push_str(&coefficient[point..]);
        }
    } else {
        output.push_str(&coefficient[..1]);
        if coefficient.len() > 1 {
            output.push('.');
            output.push_str(&coefficient[1..]);
        }
        output.push('e');
        output.push(if scientific < 0 { '-' } else { '+' });
        let exponent_digits = scientific.unsigned_abs().to_string();
        if exponent_digits.len() < 2 {
            output.push('0');
        }
        output.push_str(&exponent_digits);
    }
    if output.len() > 24 {
        return Err(invalid);
    }
    Ok(output)
}
