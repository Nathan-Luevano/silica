use std::path::Path;

use silica_oracles::capstone::CapstoneOracle;
use silica_oracles::llvm::LlvmOracle;
use silica_oracles::spec::SpecOracle;
use silica_oracles::unicorn::UnicornOracle;
use silica_oracles::Oracle;

pub fn build_oracle(name: &str, decode_table: &Path) -> Result<Box<dyn Oracle>, String> {
    match name {
        "capstone" => Ok(Box::new(CapstoneOracle::new()?)),
        "llvm" => Ok(Box::new(LlvmOracle::new()?)),
        "spec" => Ok(Box::new(SpecOracle::from_file(decode_table)?)),
        "unicorn" => Ok(Box::new(UnicornOracle::new()?)),
        other => Err(format!("unknown oracle: {other}")),
    }
}
