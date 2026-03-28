use std::process::Command;

fn main() {
    let libdir = Command::new("llvm-config")
        .arg("--libdir")
        .output()
        .expect("llvm-config --libdir failed; is the micromamba env active?");
    let libdir = String::from_utf8(libdir.stdout).unwrap();
    let libdir = libdir.trim().to_string();

    println!("cargo:rustc-link-search=native={libdir}");
    println!("cargo:rustc-link-lib=dylib=LLVM");
    println!("cargo:rustc-link-lib=dylib=capstone");
    println!("cargo:rustc-link-lib=dylib=unicorn");
    println!("cargo:rustc-link-arg=-Wl,-rpath,{libdir}");
}
