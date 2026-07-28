"""Generate C++ project scaffold from Jinja2 templates."""

import pathlib

import jinja2


class ScaffoldGenerator:
    """Generate Layer 0-1 project scaffold files (CMakeLists.txt, main.cpp, config_loader.h, config.json)."""

    def __init__(self) -> None:
        template_dir = pathlib.Path(__file__).parent / "templates"
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            keep_trailing_newline=True,
        )

    def generate(self, context: dict[str, object], output_dir: pathlib.Path) -> list[pathlib.Path]:
        """Render all scaffold templates and write to output_dir.

        Args:
            context: Jinja2 template context dict with keys:
                project_name, compile_flags, dependencies, dep_headers,
                stages, extra_sources, config.
            output_dir: Directory to write generated files.

        Returns:
            List of paths to generated files.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        generated: list[pathlib.Path] = []

        templates = {
            "cmake/CMakeLists.txt.j2": "CMakeLists.txt",
            "main/main.cpp.j2": "main.cpp",
            "main/config_loader.h.j2": "config_loader.h",
            "config/config.json.j2": "config.json",
        }

        for template_name, output_name in templates.items():
            template = self._env.get_template(template_name)
            content = template.render(**context)
            filepath = output_dir / output_name
            filepath.write_text(content)
            generated.append(filepath)

        return generated
