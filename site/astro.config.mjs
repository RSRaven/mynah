// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// GitHub Pages project site: https://rsraven.github.io/mynah/
// (drop `base` and change `site` if a custom domain is added later via CNAME)
export default defineConfig({
  site: 'https://rsraven.github.io',
  base: '/mynah/',
  trailingSlash: 'ignore',
  integrations: [
    starlight({
      title: 'Mynah',
      description:
        'Local, GPU-accelerated push-to-talk voice typing for Windows. Hold a key, talk, and the text is typed at your cursor — free, open-source, 100% offline.',
      logo: {
        src: './src/assets/mynah-logo.png',
        alt: 'Mynah',
      },
      favicon: '/brand/favicon-32.png',
      customCss: ['./src/styles/theme.css'],
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/RSRaven/mynah',
        },
      ],
      editLink: {
        baseUrl: 'https://github.com/RSRaven/mynah/edit/master/site/',
      },
      sidebar: [
        {
          label: 'Get started',
          items: [{ autogenerate: { directory: 'get-started' } }],
        },
        {
          label: 'Using Mynah',
          items: [{ autogenerate: { directory: 'using-mynah' } }],
        },
        {
          label: 'How it works',
          items: [{ autogenerate: { directory: 'how-it-works' } }],
        },
        {
          label: 'Reference',
          items: [{ autogenerate: { directory: 'reference' } }],
        },
        { label: 'Troubleshooting', slug: 'troubleshooting' },
        {
          label: 'Project',
          items: [{ autogenerate: { directory: 'project' } }],
        },
      ],
    }),
  ],
});
