//! Exact, unrounded numeric input text for the private SQL parity prototype.
//!
//! This module accepts already-correct typed input. It neither decodes JSON nor
//! enforces PostgreSQL scale or range; the unchanged numeric column owns those.

const MAX_FLOAT_TEXT_BYTES: usize = 1385;

/// Preserve integer inputs without conversion through binary floating point.
pub enum NumericInput {
    Signed(i64),
    Unsigned(u64),
    Float(f64),
}

/// Closed errors contain no input value or generated numeric text.
#[derive(Debug, PartialEq, Eq)]
pub enum NumericInputError {
    NonFinite,
    FormattingBound,
}

/// Produce the exact input value, leaving rounding and range checks to SQL.
///
/// Every finite binary64 value is an integer multiple of 2^-1074, so 1074
/// fractional decimal places represent it exactly. The untrimmed ASCII output
/// has at most one sign, 309 integer digits, one point and 1074 fractional digits.
pub fn exact_numeric_text(input: NumericInput) -> Result<String, NumericInputError> {
    match input {
        NumericInput::Signed(value) => Ok(value.to_string()),
        NumericInput::Unsigned(value) => Ok(value.to_string()),
        NumericInput::Float(value) => {
            if !value.is_finite() {
                return Err(NumericInputError::NonFinite);
            }
            let mut text = format!("{value:.1074}");
            if text.len() > MAX_FLOAT_TEXT_BYTES || !text.is_ascii() {
                return Err(NumericInputError::FormattingBound);
            }
            if text.contains('.') {
                let length = text.trim_end_matches('0').trim_end_matches('.').len();
                text.truncate(length);
            }
            Ok(text)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{MAX_FLOAT_TEXT_BYTES, NumericInput, NumericInputError, exact_numeric_text};

    // Independent goldens were generated using integer IEEE754 decoding:
    // normal: ((1 << 52) + fraction) * 2^(exponent - 1023 - 52);
    // subnormal: fraction * 2^-1074. For a denominator 2^q, the decimal
    // coefficient is numerator * 5^q, with q fractional places. No float
    // formatter, Decimal conversion or SQL rounding produced these constants.
    const HALF_CENT: &str = "0.005000000000000000104083408558608425664715468883514404296875";
    const ONE_AND_HALF_CENTS: &str = "0.01499999999999999944488848768742172978818416595458984375";
    const MIN_SUBNORMAL: &str = concat!(
        "0.000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000494065645841246544176568792868221372365059802614324764425585682500675507270",
        "20875186529983636163599237979656469544571773092665671035593979639877479601078187",
        "81263007131903114045278458171678489821036887186360569987307230500063874091535649",
        "84387312473397273169615140031715385398074126238565591171026658556686768187039560",
        "31062493194527159149245532930545654440112748012970999954193198940908041656332452",
        "47571478690147267801593552386115501348035264934720193790268107107491703332226844",
        "75333572083243193609238289345836806010601150616980975307834227731832924790498252",
        "47307763759272478746560847782037344696995336470179726777175851256605511991315048",
        "91101451037862738167250955837389733598993664809941164205702637090279242767544565",
        "229087538682506419718265533447265625",
    );
    const MAX_SUBNORMAL: &str = concat!(
        "0.000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000022250738585",
        "07200889024586876085859887650423112240959465493524802562440009228235695178775888",
        "80375915526423097809504343120858773871583572918219930202943792242235598198275012",
        "42041788969571311791082261043971979604000454897391938079198936081525613113376149",
        "84204327175103362739154978273159414382813627511383860409424946494228631669542910",
        "50802018159266421349966065178030950759130587198464239060686371020051087232827846",
        "78843631944515866135041223479014792369585208321597621066375401613736583044193603",
        "71477835530668283453563400507407304013560296804637591858316312422452159926254649",
        "43008368518617194224176464551371354201322170313704965832101546540680353974179060",
        "22589503023501937519773030945763173210852507299305089761582519159720757232455434",
        "770912461317493580281734466552734375",
    );
    const MIN_NORMAL: &str = concat!(
        "0.000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000022250738585",
        "07201383090232717332404064219215980462331830553327416887204434813918195854283159",
        "01251102056406733973103581100515243416155346010885601238537771882113077799353200",
        "23304796101474425836360719215650469425037342083752508066506166581589487204911799",
        "68591639648500635908770118304874799780887753749949451580451605050915399856582470",
        "81864511353793580499211598108576605199243335211435239014879569960959128889160299",
        "26415110634663133936634775865130293717620473256317814856643508721228286376420448",
        "46811407613911477062801689853244110024161447421618567166150540154285084716752901",
        "90316132277889672970737312333408698898317506783884692609277397797285865965494109",
        "1369095406136467568702398678315290680984617210924625396728515625",
    );
    const MAX_FINITE: &str = concat!(
        "17976931348623157081452742373170435679807056752584499659891747680315726078002853",
        "87605895586327668781715404589535143824642343213268894641827684675467035375169860",
        "49910576551282076245490090389328944075868508455133942304583236903222948165808559",
        "332123348274797826204144723168738177180919299881250404026184124858368",
    );

    #[test]
    fn exact_half_cent_inputs() -> Result<(), NumericInputError> {
        for (bits, expected) in [
            (0x3f74_7ae1_47ae_147b, HALF_CENT),
            (0x3f8e_b851_eb85_1eb8, ONE_AND_HALF_CENTS),
        ] {
            let positive = exact_numeric_text(NumericInput::Float(f64::from_bits(bits)))?;
            let negative = exact_numeric_text(NumericInput::Float(f64::from_bits(
                bits | (1_u64 << 63),
            )))?;
            assert!(positive == expected);
            assert!(negative == format!("-{expected}"));
        }
        Ok(())
    }

    #[test]
    fn integers_preserve_full_width() -> Result<(), NumericInputError> {
        for (value, expected) in [
            (i64::MIN, "-9223372036854775808"),
            (i64::MAX, "9223372036854775807"),
            (9_007_199_254_740_991, "9007199254740991"),
            (9_007_199_254_740_993, "9007199254740993"),
            (-9_007_199_254_740_993, "-9007199254740993"),
            (100, "100"),
            (-100, "-100"),
            (0, "0"),
        ] {
            assert!(exact_numeric_text(NumericInput::Signed(value))? == expected);
        }
        for (value, expected) in [
            (u64::MAX, "18446744073709551615"),
            (9_007_199_254_740_991, "9007199254740991"),
            (9_007_199_254_740_993, "9007199254740993"),
            (100, "100"),
            (0, "0"),
        ] {
            assert!(exact_numeric_text(NumericInput::Unsigned(value))? == expected);
        }
        Ok(())
    }

    #[test]
    fn zeros_and_float_integer_trailing_zeros() -> Result<(), NumericInputError> {
        for (value, expected) in [
            (0.0, "0"),
            (-0.0, "-0"),
            (100.0, "100"),
            (-100.0, "-100"),
        ] {
            assert!(exact_numeric_text(NumericInput::Float(value))? == expected);
        }
        Ok(())
    }

    #[test]
    fn finite_extremes_are_exact_and_bounded() -> Result<(), NumericInputError> {
        for (bits, expected) in [
            (0x0000_0000_0000_0001, MIN_SUBNORMAL),
            (0x000f_ffff_ffff_ffff, MAX_SUBNORMAL),
            (0x0010_0000_0000_0000, MIN_NORMAL),
            (0x7fef_ffff_ffff_ffff, MAX_FINITE),
        ] {
            for (signed_bits, signed_expected) in [
                (bits, expected.to_string()),
                (bits | (1_u64 << 63), format!("-{expected}")),
            ] {
                let actual = exact_numeric_text(NumericInput::Float(f64::from_bits(signed_bits)))?;
                assert!(actual == signed_expected);
                assert!(actual.is_ascii());
                assert!(actual.len() <= MAX_FLOAT_TEXT_BYTES);
                assert!(!actual.contains('e') && !actual.contains('E'));
            }
        }
        Ok(())
    }

    #[test]
    fn nonfinite_inputs_are_rejected() -> Result<(), NumericInputError> {
        for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(matches!(
                exact_numeric_text(NumericInput::Float(value)),
                Err(NumericInputError::NonFinite)
            ));
        }
        Ok(())
    }
}
