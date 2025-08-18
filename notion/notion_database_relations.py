import json
import os
import re
from pathlib import Path

def is_notion_database_id(value):
    """Check if a value looks like a Notion database ID."""
    if isinstance(value, str):
        # Check for UUID format with hyphens
        uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        if re.match(uuid_pattern, value):
            return True
        
        # Check for 32-character hex string (without hyphens)
        if len(value) == 32 and all(c in '0123456789abcdef' for c in value.lower()):
            return True
    
    return False

def load_database_list():
    """Load the database list to map database IDs to Supabase table names."""
    database_list_path = Path(__file__).parent / "notion_database_list.json"
    
    try:
        with open(database_list_path, 'r', encoding='utf-8') as f:
            database_list = json.load(f)
        
        # Create a mapping from database ID to Supabase table name
        # Normalize IDs by removing hyphens for comparison
        id_to_table_map = {}
        for db in database_list:
            db_id = db.get('id', '')
            supabase_table = db.get('supabase_table', '')
            if db_id and supabase_table:
                # Store both with and without hyphens for lookup
                normalized_id = db_id.replace('-', '')
                id_to_table_map[normalized_id] = supabase_table
                id_to_table_map[db_id] = supabase_table  # Also store original format
        
        print(f"📋 Loaded {len(id_to_table_map)} database ID to table mappings")
        return id_to_table_map
        
    except Exception as e:
        print(f"❌ Error loading database list: {e}")
        return {}

def extract_relationship_fields_from_structure(structure_data, id_to_table_map):
    """Extract fields that are relation types from database structure."""
    relationship_fields = []
    
    properties = structure_data.get('properties', {})
    
    for field_name, field_details in properties.items():
        field_type = field_details.get('type', '')
        
        # Check if it's a relation field
        if field_type == 'relation':
            # Get the related database ID if available
            database_id = field_details.get('database_id', '')
            
            # Look up the corresponding Supabase table name
            # Try both with and without hyphens
            related_supabase_table = id_to_table_map.get(database_id, '')
            if not related_supabase_table:
                # Try without hyphens
                normalized_id = database_id.replace('-', '')
                related_supabase_table = id_to_table_map.get(normalized_id, '')
            
            relationship_fields.append({
                "field_name": field_name,
                "related_database_id": database_id.replace('-', ''),
                "related_supabase_table": related_supabase_table
            })
    
    return relationship_fields

def extract_database_id_from_filename(filename):
    """Extract database ID from filename like 'articles_67fbcee66711465c852ebf97303787a3_structure'."""
    if '_structure' in filename:
        # Remove '_structure' suffix
        base_name = filename.replace('_structure', '')
        # Split by underscore and get the last part (the ID)
        parts = base_name.split('_')
        if len(parts) >= 2:
            # Get the last part which should be the UUID
            database_id = parts[-1]
            return database_id
    return None

def main():
    """Extract relationships from existing database structure files."""
    print("🔍 Extracting relationships from database structure files...")
    
    # Load database ID to Supabase table mapping
    id_to_table_map = load_database_list()
    
    # Path to database samples directory (relative to script location)
    samples_dir = Path(__file__).parent / "database_sample"
    
    if not samples_dir.exists():
        print(f"❌ Database samples directory not found: {samples_dir}")
        return
    
    all_relations = []
    
    # Find all structure files
    structure_files = list(samples_dir.glob("*_structure.json"))
    print(f"📋 Found {len(structure_files)} structure files")
    
    for structure_file in structure_files:
        try:
            # Extract database ID from filename
            filename = structure_file.stem  # Remove .json extension
            database_id = extract_database_id_from_filename(filename)
            
            if not database_id:
                print(f"⚠️ Could not extract database ID from filename: {filename}")
                continue
            
            print(f"📊 Processing: {database_id}")
            
            # Get the origin Supabase table name
            origin_supabase_table = id_to_table_map.get(database_id, '')
            if not origin_supabase_table:
                # Try without hyphens
                normalized_id = database_id.replace('-', '')
                origin_supabase_table = id_to_table_map.get(normalized_id, '')
            
            # Load structure data
            with open(structure_file, 'r', encoding='utf-8') as f:
                structure_data = json.load(f)
            
            # Extract relationships from the structure
            relationship_fields = extract_relationship_fields_from_structure(structure_data, id_to_table_map)
            
            if relationship_fields:
                print(f"  🔗 Found {len(relationship_fields)} relationship fields")
                
                # Create the relationship structure
                database_relations = {
                    "origin_database_id": database_id,
                    "origin_supabase_table": origin_supabase_table,
                    "relations": relationship_fields
                }
                
                all_relations.append(database_relations)
            else:
                print(f"  ⚠️ No relationship fields found")
                
        except Exception as e:
            print(f"❌ Error processing {structure_file.name}: {e}")
            continue
    
    # Save the complete relationship summary
    output_file = Path(__file__).parent / "notion_database_relations.json"
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_relations, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Relationship extraction completed!")
    print(f"📊 Processed {len(all_relations)} databases")
    print(f"💾 Results saved to: {output_file.name}")
    
    # Show example output
    if all_relations:
        print(f"\n📋 Example output format:")
        example = all_relations[0]
        print(json.dumps(example, indent=2))

if __name__ == "__main__":
    main()
