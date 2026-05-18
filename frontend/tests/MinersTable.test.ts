import '@testing-library/jest-dom';
import { render } from '@testing-library/svelte';
import { describe, expect, test } from 'vitest';
import MinersTable from '$lib/components/MinersTable.svelte';
import type { Machine } from '$lib/api/types';

describe('MinersTable', () => {
    test('shows skip total with runtime breakdown title', () => {
        const machines: Machine[] = [
            {
                host_id: 'host-a',
                roles: ['train'],
                hotkeys: ['hk-a'],
                workers: [
                    {
                        role: 'train',
                        status: 'live',
                        miner: {},
                        worker: { hotkey_ss58: 'hk-a', worker_id: 'gpu0', host_id: 'host-a' },
                        chain: null,
                        last_seen_unix: 1,
                        age_sec: 1,
                        n_receipts: 0,
                        queue_depth: 2,
                        queue_cap: 8,
                        at_cap: false,
                        runtime: {
                            assigned_depth: 2,
                            skipped: { missing_grant: 3, bad_owner_signature: 1 }
                        },
                        sources: ['heartbeat']
                    }
                ],
                last_seen_unix: 1,
                age_sec: 1
            }
        ];
        const { getByTitle } = render(MinersTable, { props: { machines } });
        expect(getByTitle('missing_grant: 3, bad_owner_signature: 1')).toHaveTextContent('4');
    });
});
