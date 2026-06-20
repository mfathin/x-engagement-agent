import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class SkillManager:
    """Manages the SKILLS.md file which serves as the AI's persistent memory."""

    def __init__(self, filepath: str = "SKILLS.md"):
        self.filepath = Path(filepath)
        # Ensure file exists
        if not self.filepath.exists():
            with open(self.filepath, "w", encoding="utf-8") as f:
                f.write("# AI Agent Skills & Niche Memory\n\n")

    def get_skills(self) -> str:
        """Read current skills."""
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return content if content else "Belum ada skill atau niche yang dipelajari."
        except Exception as e:
            logger.error(f"Failed to read SKILLS.md: {e}")
            return ""

    def update_skills(self, new_learning: str) -> None:
        """Append a new learning or insight to the skills file."""
        if not new_learning:
            return
            
        try:
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(f"\n- {new_learning}\n")
            logger.info("✅ SKILLS.md updated with new learning.")
        except Exception as e:
            logger.error(f"Failed to update SKILLS.md: {e}")

    def overwrite_niche(self, niche_context: str) -> None:
        """Overwrite the niche section to keep only the latest one, avoiding prompt bloat."""
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Reconstruct content without old NICHE UPDATEs
            lines = content.split('\n')
            new_lines = []
            skip_mode = False
            for line in lines:
                # Start skipping if we see a niche update
                if line.startswith("- [NICHE UPDATE"):
                    skip_mode = True
                # Stop skipping if we see another type of skill
                elif line.startswith("- [PERSONA]") or line.startswith("- [POST MORTEM LEARNING]"):
                    skip_mode = False
                
                if not skip_mode:
                    new_lines.append(line)
            
            # Remove trailing empty lines
            while new_lines and not new_lines[-1].strip():
                new_lines.pop()
                
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            with open(self.filepath, "w", encoding="utf-8") as f:
                # Re-write the filtered content and append the NEW niche
                f.write("\n".join(new_lines).strip() + f"\n\n- [NICHE UPDATE {now}]\n{niche_context}\n")
                
            logger.info("✅ SKILLS.md niche overwritten with latest trend.")
        except Exception as e:
            logger.error(f"Failed to overwrite niche in SKILLS.md: {e}")
