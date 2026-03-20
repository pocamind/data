use crate::ctx::TransformContext;

mod mutate;
mod ctx;

fn main() -> anyhow::Result<()> {
    println!("transform");

    let ctx = TransformContext::new()?;

    ctx.write_back()?;

    Ok(())
}
