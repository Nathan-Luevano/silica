use std::fs::File;
use std::io::Read;
use std::path::Path;

use crate::Oracle;

#[derive(Debug, Clone)]
pub struct Form {
    pub psname: String,
    pub encoding_names: String,
    pub mnemonic: String,
    pub gating: String,
}

#[derive(Debug, Clone, Copy)]
pub struct Node {
    pub kind: u8,
    pub bit: u8,
    pub a: u32,
    pub b: u32,
}

pub struct SpecOracle {
    pub spec_release: String,
    pub forms: Vec<Form>,
    pub nodes: Vec<Node>,
    pub root_index: usize,
}

impl SpecOracle {
    // parses decode-table.bin format described in docs/formats.md
    pub fn from_bytes(data: &[u8]) -> Result<Self, String> {
        let mut cursor = 0;

        if data.len() < 4 || &data[0..4] != b"SIL1" {
            return Err("invalid magic, expected SIL1".to_string());
        }
        cursor += 4;

        let read_u32 = |c: &mut usize| -> Result<u32, String> {
            if *c + 4 > data.len() {
                return Err("unexpected eof reading u32".to_string());
            }
            let val = u32::from_le_bytes(data[*c..*c + 4].try_into().unwrap());
            *c += 4;
            Ok(val)
        };

        let read_string = |c: &mut usize| -> Result<String, String> {
            let len = read_u32(c)? as usize;
            if *c + len > data.len() {
                return Err("unexpected eof reading string".to_string());
            }
            let s = std::str::from_utf8(&data[*c..*c + len])
                .map_err(|e| e.to_string())?
                .to_string();
            *c += len;
            Ok(s)
        };

        let spec_release = read_string(&mut cursor)?;
        let form_count = read_u32(&mut cursor)? as usize;

        let mut forms = Vec::with_capacity(form_count);
        for _ in 0..form_count {
            let psname = read_string(&mut cursor)?;
            let encoding_names = read_string(&mut cursor)?;
            let mnemonic = read_string(&mut cursor)?;
            let gating = read_string(&mut cursor)?;
            forms.push(Form {
                psname,
                encoding_names,
                mnemonic,
                gating,
            });
        }

        let node_count = read_u32(&mut cursor)? as usize;
        let mut nodes = Vec::with_capacity(node_count);
        for _ in 0..node_count {
            if cursor + 12 > data.len() {
                return Err("unexpected eof reading node".to_string());
            }
            let kind = data[cursor];
            let bit = data[cursor + 1];
            // 2 pad bytes in <BBxxII
            cursor += 4;
            let a = read_u32(&mut cursor)?;
            let b = read_u32(&mut cursor)?;
            nodes.push(Node { kind, bit, a, b });
        }

        let root_index = read_u32(&mut cursor)? as usize;

        Ok(Self {
            spec_release,
            forms,
            nodes,
            root_index,
        })
    }

    pub fn from_file(path: impl AsRef<Path>) -> Result<Self, String> {
        let mut file = File::open(path).map_err(|e| e.to_string())?;
        let mut data = Vec::new();
        file.read_to_end(&mut data).map_err(|e| e.to_string())?;
        Self::from_bytes(&data)
    }

    pub fn classify(&self, word: u32) -> (bool, Option<&Form>, u32) {
        if self.nodes.is_empty() {
            return (false, None, 0);
        }
        let mut curr_idx = self.root_index;
        loop {
            let node = &self.nodes[curr_idx];
            match node.kind {
                0 => {
                    let bit_val = (word >> node.bit) & 1;
                    curr_idx = if bit_val == 0 {
                        node.a as usize
                    } else {
                        node.b as usize
                    };
                }
                1 => {
                    let form_idx = node.a as usize;
                    let form = self.forms.get(form_idx);
                    return (true, form, node.b);
                }
                2 => {
                    return (false, None, 0);
                }
                _ => return (false, None, 0),
            }
        }
    }
}

impl Oracle for SpecOracle {
    fn name(&self) -> &'static str {
        "spec"
    }

    fn decode(&self, word: u32) -> bool {
        self.classify(word).0
    }

    fn disassemble(&self, word: u32) -> Option<String> {
        let (allocated, form_opt, _) = self.classify(word);
        if allocated {
            form_opt.map(|f| {
                if !f.mnemonic.is_empty() {
                    f.mnemonic.clone()
                } else {
                    f.psname.clone()
                }
            })
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_spec_oracle_with_real_artifact() {
        let path = "../../artifacts/decode-table.bin";
        if !Path::new(path).exists() {
            return;
        }
        let oracle = SpecOracle::from_file(path).expect("failed to load decode-table.bin");
        assert_eq!(oracle.spec_release, "ISA_A64_xml_A_profile-2026-06_mc");
        assert_eq!(oracle.name(), "spec");

        // RET: 0xD65F03C0 with Rn=30
        let ret_word = 0xD65F03C0;
        let (allocated, form, amb) = oracle.classify(ret_word);
        assert!(allocated);
        assert_eq!(amb, 1);
        assert_eq!(
            form.unwrap().psname,
            "A64.control.branch_reg.RET_64R_branch_reg"
        );
        assert_eq!(form.unwrap().mnemonic, "RET");
        assert!(oracle.decode(ret_word));
        assert_eq!(oracle.disassemble(ret_word), Some("RET".to_string()));

        // NOP: 0xD503201F
        let nop_word = 0xD503201F;
        assert!(oracle.decode(nop_word));

        // 0x00000000 is UDF (allocated in spec)
        assert!(oracle.decode(0x00000000));
        assert_eq!(oracle.disassemble(0x00000000), Some("UDF".to_string()));

        // 0x00010000 is unallocated in AArch64
        assert!(!oracle.decode(0x00010000));
        assert_eq!(oracle.disassemble(0x00010000), None);
    }
}
