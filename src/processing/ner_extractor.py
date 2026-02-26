"""
AIMScribe AI Backend - NER Extractor Module
Handles clinical entity extraction using LangChain and COT Prompts.
Optimized with parallel module execution using ThreadPoolExecutor.
"""

import logging
import json
import time
from typing import Dict, List, Optional, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_openai import AzureChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser

from config import settings
from prompts.loader import PromptLoader, get_prompt_loader

logger = logging.getLogger(__name__)

# Maximum workers for parallel NER extraction (9 modules)
MAX_NER_WORKERS = 9


class NERExtractor:
    """
    Extracts clinical entities using COT prompts and few-shot examples.
    """
    
    def __init__(self):
        self.llm = AzureChatOpenAI(
            azure_endpoint=settings.azure_ner_endpoint,
            api_key=settings.azure_ner_api_key,
            api_version=settings.azure_api_version,
            deployment_name=settings.azure_ner_deployment,
            # temperature=0.1,  # GPT-5.2 only supports default temperature (1)
            max_completion_tokens=4000  # GPT-5.2 requires max_completion_tokens instead of max_tokens
        )
        self.prompt_loader = get_prompt_loader()
        
    def extract_all(
        self,
        transcription: str,
        patient_context: Dict = None,
        previous_medications: List[Dict] = None
    ) -> Dict[str, Any]:
        """
        Run full NER pipeline with PARALLEL module extraction.

        Args:
            transcription: Full conversation text
            patient_context: Database baseline data (demographics, etc.)
            previous_medications: List of meds from prev visit (for "continue same" logic)

        Returns:
            Complete NER JSON
        """
        start_time = time.time()
        logger.info("Starting NER extraction pipeline (parallel mode)...")

        # 1. Initialize result structure with patient context
        result = self._init_structure(patient_context)

        # 2. Prepare extraction tasks
        hist_context = json.dumps(previous_medications, ensure_ascii=False) if previous_medications else None

        # Define all extraction tasks: (module_name, result_key, kwargs)
        extraction_tasks = [
            ("chief_complaints", "Chief Complaints (English)", {}),
            ("medications", "Medications", {"previous_medications": hist_context}),
            ("diagnosis", "Diagnosis (English)", {}),
            ("examination", "_examination", {}),  # Special handling needed
            ("investigations", "Investigations (English)", {}),
            ("advice", "Advice (Bengali)", {}),
            ("follow_up", "Follow Up (Bengali)", {}),
            ("drug_history", "_drug_history", {}),  # Special handling needed
            ("additional_notes", "_additional_notes", {}),  # Special handling needed
        ]

        # 3. Execute all modules in parallel
        extraction_results = {}
        with ThreadPoolExecutor(max_workers=MAX_NER_WORKERS) as executor:
            # Submit all tasks
            future_to_task = {
                executor.submit(
                    self._extract_module,
                    task[0],  # module_name
                    transcription,
                    **task[2]  # kwargs
                ): task
                for task in extraction_tasks
            }

            # Collect results as they complete
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                module_name, result_key, _ = task
                try:
                    data = future.result()
                    extraction_results[result_key] = data
                    logger.debug(f"Module {module_name} completed")
                except Exception as e:
                    logger.error(f"Module {module_name} failed: {e}")
                    # Set safe default based on expected type
                    extraction_results[result_key] = {} if module_name in ["examination", "follow_up"] else []

        # 4. Merge results into final structure
        # Direct mappings
        result["Chief Complaints (English)"] = extraction_results.get("Chief Complaints (English)", [])
        result["Medications"] = extraction_results.get("Medications", [])
        result["Diagnosis (English)"] = extraction_results.get("Diagnosis (English)", [])
        result["Investigations (English)"] = extraction_results.get("Investigations (English)", [])
        result["Advice (Bengali)"] = extraction_results.get("Advice (Bengali)", [])
        result["Follow Up (Bengali)"] = extraction_results.get("Follow Up (Bengali)", {})

        # Special handling: Examination
        exam_data = extraction_results.get("_examination", {})
        if exam_data:
            result["Examination (English)"]["O/E (English)"] = exam_data.get("O/E (English)", {})
            result["Examination (English)"]["S/E (English)"] = exam_data.get("S/E (English)", {})

        # Special handling: Drug History
        drug_history = extraction_results.get("_drug_history", [])
        if drug_history:
            result["Examination (English)"]["Drug History (English)"] = drug_history

        # Special handling: Additional Notes (flatten object to list)
        notes_data = extraction_results.get("_additional_notes", {})
        if notes_data:
            flat_notes = []
            if notes_data.get("Family History"):
                flat_notes.extend([f"Family: {x}" for x in notes_data["Family History"]])
            if notes_data.get("Prior Episodes"):
                flat_notes.extend([f"History: {x}" for x in notes_data["Prior Episodes"]])
            if notes_data.get("Allergies"):
                flat_notes.extend([f"Allergy: {x}" for x in notes_data["Allergies"]])
            result["Examination (English)"]["Additional Notes (English)"] = flat_notes

        elapsed = time.time() - start_time
        logger.info(f"NER extraction completed in {elapsed:.2f}s (parallel mode)")
        return result

    def _extract_module(
        self,
        prompt_name: str,
        transcription: str,
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs
    ) -> Any:
        """
        Run a single extraction module with retry logic.

        Args:
            prompt_name: Name of the prompt template
            transcription: The transcription text
            max_retries: Maximum number of retry attempts
            base_delay: Base delay for exponential backoff (seconds)
            **kwargs: Additional template variables

        Returns:
            Extracted data (dict or list depending on module)
        """
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                # Load and render prompt
                system_msg = self.prompt_loader.load(
                    "ner",
                    prompt_name,
                    transcription=transcription,
                    **kwargs
                )

                # Call LLM
                messages = [
                    SystemMessage(content=system_msg)
                ]

                response = self.llm.invoke(messages)
                content = response.content.strip()

                # Clean markdown code blocks if present
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()

                # Parse JSON
                try:
                    data = json.loads(content)
                    return data
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse JSON for {prompt_name}: {content[:100]}...")
                    return [] if prompt_name not in ["examination", "follow_up"] else {}

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Check if it's a rate limit or transient error
                is_retryable = any(x in error_str for x in [
                    "rate limit", "429", "timeout", "connection",
                    "temporary", "overloaded", "503", "500"
                ])

                if is_retryable and attempt < max_retries:
                    # Exponential backoff: 1s, 2s, 4s
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Module {prompt_name} failed (attempt {attempt + 1}/{max_retries + 1}), "
                        f"retrying in {delay:.1f}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    # Non-retryable error or max retries reached
                    break

        logger.error(f"Module {prompt_name} failed after {max_retries + 1} attempts: {last_error}")
        # Return safe empty defaults based on expected type
        return {} if prompt_name in ["examination", "follow_up"] else []

    def _init_structure(self, ctx: Dict = None) -> Dict:
        """Initialize empty JSON structure with patient demographics."""
        ctx = ctx or {}
        demographics = ctx.get('demographics', {})

        return {
            "Patient Info (English)": {
                "Name (English)": demographics.get('name', ''),
                "Age (English)": demographics.get('age', ''),
                "Gender (English)": demographics.get('gender', ''),
                "Blood Group (English)": demographics.get('blood_group', ''),
                "Last Visit Date (English)": ctx.get('last_visit_date', ''),
                "Consultation Date (English)": ""
            },
            "Chief Complaints (English)": [],
            "Examination (English)": {
                "O/E (English)": {
                    "ANEMIA. (English)": "",
                    "DEHYDRATION. (English)": "",
                    "JAUNDICE. (English)": "",
                    "EDEMA. (English)": ""
                },
                "S/E (English)": {
                    "Heart (English)": "",
                    "Lung (English)": "",
                    "Abdomen (English)": ""
                },
                "Drug History (English)": [],
                "Additional Notes (English)": []
            },
            "Investigations (English)": [],
            "Diagnosis (English)": [],
            "Medications": [],
            "Advice (Bengali)": [],
            "Follow Up (Bengali)": {
                "Next Consultation Date (Bengali)": ""
            }
        }
