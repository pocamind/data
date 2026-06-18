use crate::ctx::TransformContext;

mod ctx;
mod mutate;

fn main() -> anyhow::Result<()> {
    let mut ctx = TransformContext::new()?;

    // delete all 'flamecharmer' instances from prereqs
    ctx.replace_prereqs("flamecharmer", None);

    ctx.write_back()?;

    Ok(())
}
