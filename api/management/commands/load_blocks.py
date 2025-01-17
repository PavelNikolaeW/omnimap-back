import json
import uuid
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from api.models import Block

User = get_user_model()


class Command(BaseCommand):
    help = 'Load blocks from a JSON file into the database'

    def add_arguments(self, parser):
        parser.add_argument('json_file', type=str, help='Path to the JSON file containing blocks data')

    def handle(self, *args, **options):
        json_file = options['json_file']
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                blocks_json = json.load(f)
        except FileNotFoundError:
            raise CommandError(f"File '{json_file}' does not exist")
        except json.JSONDecodeError as e:
            raise CommandError(f"Invalid JSON format: {e}")

        # Create a mapping from UUID to block data
        blocks_dict = {block['uuid']: block for block in blocks_json}

        # Prepare a mapping from UUID to Block instance
        created_blocks = {}

        # First pass: create all blocks without setting parent
        self.stdout.write(self.style.NOTICE('Creating Block instances...'))
        for uuid_str, block_data in blocks_dict.items():
            # Get or create the user
            creator_identifier = block_data.get('creator')
            if not creator_identifier:
                self.stdout.write(self.style.WARNING(f"Block {uuid_str} has no creator. Skipping."))
                continue
            try:
                creator = User.objects.get(username=creator_identifier)
            except User.DoesNotExist:
                # Optionally, create the user or handle the error
                self.stdout.write(self.style.WARNING(f"User '{creator_identifier}' does not exist. Creating user."))
                creator = User.objects.create(username=creator_identifier)

            # Parse the UUID
            try:
                block_uuid = uuid.UUID(uuid_str)
            except ValueError:
                self.stdout.write(self.style.ERROR(f"Invalid UUID '{uuid_str}'. Skipping block."))
                continue

            # Create or update the Block instance
            block, created = Block.objects.update_or_create(
                id=block_uuid,
                defaults={
                    'title': block_data.get('title'),
                    'data': block_data.get('data', {}),
                    'creator': creator,
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created Block '{block.title}' with UUID {block.id}"))
            else:
                self.stdout.write(self.style.WARNING(f"Updated Block '{block.title}' with UUID {block.id}"))
            created_blocks[uuid_str] = block

        # Second pass: set parent relationships
        self.stdout.write(self.style.NOTICE('Setting parent relationships...'))
        for uuid_str, block_data in blocks_dict.items():
            block = created_blocks.get(uuid_str)
            if not block:
                self.stdout.write(
                    self.style.ERROR(f"Block with UUID {uuid_str} was not created. Skipping parent assignment."))
                continue

            # Determine the parent: find if any block lists this block as a child
            parent_uuid = None
            for potential_parent_uuid, potential_parent_data in blocks_dict.items():
                if uuid_str in potential_parent_data.get('children', []):
                    parent_uuid = potential_parent_uuid
                    break
            if parent_uuid:
                parent_block = created_blocks.get(parent_uuid)
                if parent_block:
                    block.parent = parent_block
                    block.save()
                    self.stdout.write(
                        self.style.SUCCESS(f"Set parent of Block '{block.title}' to '{parent_block.title}'"))
                else:
                    self.stdout.write(
                        self.style.ERROR(f"Parent block with UUID {parent_uuid} not found for Block '{block.title}'"))
            else:
                # No parent found; assume it's a root block
                block.parent = None
                block.save()
                self.stdout.write(self.style.SUCCESS(f"Set parent of Block '{block.title}' to None (root block)"))

        self.stdout.write(self.style.SUCCESS('Successfully loaded blocks into the database.'))