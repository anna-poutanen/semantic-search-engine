# Third-party notices for published benchmark material

Public benchmark datasets, source excerpts, retrieval/context exports, judgments,
reference patches and coding-workflow patches may reproduce portions of the
seven upstream projects below. These notices apply to those upstream portions;
project-original code remains `UNLICENSED`. No project-wide license is granted
by this file. Modified implementation and reference patches are experiment
artifacts based on the pinned upstream revisions, rather than unmodified
upstream releases; their associated task/run records identify the changes.

All license texts below were read from the retained pinned Git objects locally.
Each is byte-for-byte equal to the existing `benchmarks/*_LICENSE.txt` copy.
The ignored corpus checkouts are fetched separately and are not redistributed
as additional complete source trees. Source-specific attributions found within
the declared indexed implementation scopes are retained after the full licenses.
Other downloaded models/runtimes and installed dependencies are described in
[the existing repository notices](../THIRD_PARTY_NOTICES.md).

## Pallets Flask — BSD-3-Clause

Pinned commit: `d086db856be187255b8ec61ef409357393020f32`. Indexed implementation scope: `src/flask`.

Source: [LICENSE.txt](https://github.com/pallets/flask/blob/d086db856be187255b8ec61ef409357393020f32/LICENSE.txt); existing copy: [FLASK_LICENSE.txt](../benchmarks/FLASK_LICENSE.txt).
Git blob: `9d227a0cc43c3268d15722b763bd94ad298645a1`. License SHA-256: `489a8e1108509ed98a37bb983e11e0f7e1d31f0bd8f99a79c8448e7ff37d07ea` (1475 bytes).

```text
Copyright 2010 Pallets

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1.  Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.

2.  Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

3.  Neither the name of the copyright holder nor the names of its
    contributors may be used to endorse or promote products derived from
    this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Requests — Apache-2.0

Pinned commit: `611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60`. Indexed implementation scope: `src/requests`.

Source: [LICENSE](https://github.com/psf/requests/blob/611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60/LICENSE); existing copy: [REQUESTS_LICENSE.txt](../benchmarks/REQUESTS_LICENSE.txt).
Git blob: `67db8588217f266eb561f75fae738656325deac9`. License SHA-256: `09e8a9bcec8067104652c168685ab0931e7868f9c8284b66f5ae6edae5f1130b` (10142 bytes).

```text

                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.
```

### Requests upstream NOTICE

Source: [NOTICE](https://github.com/psf/requests/blob/611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60/NOTICE). Git blob: `1ff62db688277b77c83c1766dac7f165364d3528`. SHA-256: `f5110972dedad2b4e9d314518daf3b7d72d6e02e499acd802181de6f74571dcc` (38 bytes).

```text
Requests
Copyright 2019 Kenneth Reitz
```

## Pallets Click — BSD-3-Clause

Pinned commit: `934813e4d421071a1b3db3973c02fe2721359a6e`. Indexed implementation scope: `src/click`.

Source: [LICENSE.txt](https://github.com/pallets/click/blob/934813e4d421071a1b3db3973c02fe2721359a6e/LICENSE.txt); existing copy: [CLICK_LICENSE.txt](../benchmarks/CLICK_LICENSE.txt).
Git blob: `d12a849186982399c537c5b9a8fd77bf2edd5eab`. License SHA-256: `9a8ad106a394e853bfe21f42f4e72d592819a22805d991b5f3275029292b658d` (1475 bytes).

```text
Copyright 2014 Pallets

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1.  Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.

2.  Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

3.  Neither the name of the copyright holder nor the names of its
    contributors may be used to endorse or promote products derived from
    this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Starlette — BSD-3-Clause

Pinned commit: `4d72fd87ea1c358691a19468168f7217d8ca6a87`. Indexed implementation scope: `starlette`.

Source: [LICENSE.md](https://github.com/encode/starlette/blob/4d72fd87ea1c358691a19468168f7217d8ca6a87/LICENSE.md); existing copy: [STARLETTE_LICENSE.txt](../benchmarks/STARLETTE_LICENSE.txt).
Git blob: `d16a60ec5b9963ef86b35a52ac92227014618e6c`. License SHA-256: `dcb95677a02240243187e964f941847d19b17821cf99e5afae684fab328c19bf` (1518 bytes).

```text
Copyright © 2018, [Encode OSS Ltd](https://www.encode.io/).
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Rich — MIT

Pinned commit: `43d3b04725ab9731727fb1126e35980c62f32377`. Indexed implementation scope: `rich`.

Source: [LICENSE](https://github.com/Textualize/rich/blob/43d3b04725ab9731727fb1126e35980c62f32377/LICENSE); existing copy: [RICH_LICENSE.txt](../benchmarks/RICH_LICENSE.txt).
Git blob: `4415505566f261c802b671426be529a31f914137`. License SHA-256: `deed7c17a4318158190a3ea239cc879a5a50271cebb98ae7025f48fbe58dca15` (1056 bytes).

```text
Copyright (c) 2020 Will McGugan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Express — MIT

Pinned commit: `cf6722bf89a86905281d9160b6482e08166b5ef0`. Indexed implementation scope: `lib`.

Source: [LICENSE](https://github.com/expressjs/express/blob/cf6722bf89a86905281d9160b6482e08166b5ef0/LICENSE); existing copy: [EXPRESS_LICENSE.txt](../benchmarks/EXPRESS_LICENSE.txt).
Git blob: `aa927e44e31d486f807634887662efa39256bf84`. License SHA-256: `95a5762890e5c1c9808921cef095661fc482c5e1f0bba31446ac85595df6237c` (1249 bytes).

```text
(The MIT License)

Copyright (c) 2009-2014 TJ Holowaychuk <tj@vision-media.ca>
Copyright (c) 2013-2014 Roman Shtylman <shtylman+expressjs@gmail.com>
Copyright (c) 2014-2015 Douglas Christopher Wilson <doug@somethingdoug.com>

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
'Software'), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED 'AS IS', WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## Zod — MIT

Pinned commit: `b500a2ee95134f81d74da7911d7378dd2853d042`. Indexed implementation scope: `packages/zod/src/v4`.

Source: [LICENSE](https://github.com/colinhacks/zod/blob/b500a2ee95134f81d74da7911d7378dd2853d042/LICENSE); existing copy: [ZOD_LICENSE.txt](../benchmarks/ZOD_LICENSE.txt).
Git blob: `c06579627b432d09779094cd0166b7f37fe51db8`. License SHA-256: `3f1189b28e3866e0d979968d466b78f813f76827cfdca1fbb124cc0a5c8841f8` (1072 bytes).

```text
MIT License

Copyright (c) 2025 Colin McDonnell

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Source-level attributions within indexed scopes

The following are additional literal upstream notices; file paths delimit their
scope. No separate top-level `NOTICE` file was present in the six pinned
repositories other than Requests.

### Requests src/requests/api.py attribution

Source: [src/requests/api.py](https://github.com/psf/requests/blob/611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60/src/requests/api.py). Full source SHA-256: `4d15480ac046f089209798e8650476ef4a28ebe6f81b400758f8ef42ec6b5509`. Reproduced notice-span SHA-256: `18b8ca9b6906816db3df3c8d46bb6f87cc859ce1dc95f49d085be0ed0e258974`.

```text
:copyright: (c) 2012 by Kenneth Reitz.
:license: Apache2, see LICENSE for more details.
```

### Requests src/requests/__init__.py attribution

Source: [src/requests/__init__.py](https://github.com/psf/requests/blob/611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60/src/requests/__init__.py). Full source SHA-256: `80534322a1a3167d8775829ee220a6acf4fe455f3f778c4b8e94bcf7fadfa0b5`. Reproduced notice-span SHA-256: `08cfe26c4787a23e453231beb343bd1f7271f01c54ab0ae6f798a4f38f59df33`.

```text
:copyright: (c) 2017 by Kenneth Reitz.
:license: Apache 2.0, see LICENSE for more details.
```

### Requests src/requests/__version__.py attribution

Source: [src/requests/__version__.py](https://github.com/psf/requests/blob/611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60/src/requests/__version__.py). Full source SHA-256: `abba90670b0370a3dc40d97c2dea46a35d4bec941ff10d7c3cf3098ef04a17f9`. Reproduced notice-span SHA-256: `e3ab6f822927aa1d86f8f113eb20cddac875eeeab9aa92161bc2c0e39d69861c`.

```text
__license__ = "Apache-2.0"
__copyright__ = "Copyright Kenneth Reitz"
```

### Click parser: optparse authors

Source: [src/click/parser.py](https://github.com/pallets/click/blob/934813e4d421071a1b3db3973c02fe2721359a6e/src/click/parser.py). Full source SHA-256: `878b2775ca45e4e1eb65037cbc3f0859be4e072bd6ec005b8514e8c68beba92f`. Reproduced notice-span SHA-256: `16c80f2c8f9d611113d688d32ad906f921d2eafab06d4669d46fd1822e588d07`.

```text
"""
This module started out as largely a copy paste from the stdlib's
optparse module with the features removed that we do not need from
optparse because we implement them in Click on a higher level (for
instance type handling, help formatting and a lot more).

The plan is to remove more and more from here over time.

The reason this is a different module and not optparse from the stdlib
is that there are differences in 2.x and 3.x about the error messages
generated and optparse in the stdlib uses gettext for no good reason
and might cause us issues.

Click uses parts of optparse written by Gregory P. Ward and maintained
by the Python Software Foundation. This is limited to code in parser.py.

Copyright 2001-2006 Gregory P. Ward. All rights reserved.
Copyright 2002-2006 Python Software Foundation. All rights reserved.
"""

# This code uses parts of optparse written by Gregory P. Ward and
# maintained by the Python Software Foundation.
# Copyright 2001-2006 Gregory P. Ward
# Copyright 2002-2006 Python Software Foundation
```

### Rich spinner data: cli-spinners MIT notice

Source: [rich/_spinners.py](https://github.com/Textualize/rich/blob/43d3b04725ab9731727fb1126e35980c62f32377/rich/_spinners.py). Full source SHA-256: `536af5fe0ff5cd28ec8e251d00449cda200c7378b8ae2fd2f0f60fea4439cf52`. Reproduced notice-span SHA-256: `4115edcea811171856cc43a901d7a42104b949383ce3a1c3a8b9beb17cb17d7a`.

```text
"""
Spinners are from:
* cli-spinners:
    MIT License
    Copyright (c) Sindre Sorhus <sindresorhus@gmail.com> (sindresorhus.com)
    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights to
    use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
    the Software, and to permit persons to whom the Software is furnished to do so,
    subject to the following conditions:
    The above copyright notice and this permission notice shall be included
    in all copies or substantial portions of the Software.
    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
    INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
    PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
    FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
    ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
    IN THE SOFTWARE.
"""
```

### Express lib/application.js header

Source: [lib/application.js](https://github.com/expressjs/express/blob/cf6722bf89a86905281d9160b6482e08166b5ef0/lib/application.js). Full source SHA-256: `6d7e08355025786982d17042fc0355b6a5e9339545dc27e2782b3aaa28a7bc04`. Reproduced notice-span SHA-256: `51c4ad50b44cce86987c7fb58b65c7c1e89c5982a863a50d83a06199c28f881c`.

```text
/*!
 * express
 * Copyright(c) 2009-2013 TJ Holowaychuk
 * Copyright(c) 2013 Roman Shtylman
 * Copyright(c) 2014-2015 Douglas Christopher Wilson
 * MIT Licensed
 */
```

### Express lib/express.js header

Source: [lib/express.js](https://github.com/expressjs/express/blob/cf6722bf89a86905281d9160b6482e08166b5ef0/lib/express.js). Full source SHA-256: `4f35e8273a5e78c35e778d14e4a8c80a81ca3e1fc8047dc87d2077b860404572`. Reproduced notice-span SHA-256: `51c4ad50b44cce86987c7fb58b65c7c1e89c5982a863a50d83a06199c28f881c`.

```text
/*!
 * express
 * Copyright(c) 2009-2013 TJ Holowaychuk
 * Copyright(c) 2013 Roman Shtylman
 * Copyright(c) 2014-2015 Douglas Christopher Wilson
 * MIT Licensed
 */
```

### Express lib/request.js header

Source: [lib/request.js](https://github.com/expressjs/express/blob/cf6722bf89a86905281d9160b6482e08166b5ef0/lib/request.js). Full source SHA-256: `a9fda39c4472aeaa4d696f4324f58cc1d036f84ed04145d6d56a12a59578e751`. Reproduced notice-span SHA-256: `51c4ad50b44cce86987c7fb58b65c7c1e89c5982a863a50d83a06199c28f881c`.

```text
/*!
 * express
 * Copyright(c) 2009-2013 TJ Holowaychuk
 * Copyright(c) 2013 Roman Shtylman
 * Copyright(c) 2014-2015 Douglas Christopher Wilson
 * MIT Licensed
 */
```

### Express lib/response.js header

Source: [lib/response.js](https://github.com/expressjs/express/blob/cf6722bf89a86905281d9160b6482e08166b5ef0/lib/response.js). Full source SHA-256: `efe24afe2978d0bf2ad52d5b163a7af0d25d965e5dfc75979c4fcc883c60a510`. Reproduced notice-span SHA-256: `7e779fba846e3647fbe269b8ee19cf44ecc06ce1e8a81676242a40374ce06778`.

```text
/*!
 * express
 * Copyright(c) 2009-2013 TJ Holowaychuk
 * Copyright(c) 2014-2015 Douglas Christopher Wilson
 * MIT Licensed
 */
```

### Express lib/utils.js header

Source: [lib/utils.js](https://github.com/expressjs/express/blob/cf6722bf89a86905281d9160b6482e08166b5ef0/lib/utils.js). Full source SHA-256: `4bd3bf9c911e086d1911954708de7a6c384ed924360e3fd1d4a43c98bd68b112`. Reproduced notice-span SHA-256: `7e779fba846e3647fbe269b8ee19cf44ecc06ce1e8a81676242a40374ce06778`.

```text
/*!
 * express
 * Copyright(c) 2009-2013 TJ Holowaychuk
 * Copyright(c) 2014-2015 Douglas Christopher Wilson
 * MIT Licensed
 */
```

### Express lib/view.js header

Source: [lib/view.js](https://github.com/expressjs/express/blob/cf6722bf89a86905281d9160b6482e08166b5ef0/lib/view.js). Full source SHA-256: `74f4171b66263e22481820bc5975708f7dd8a61484f570aac7c5b4ab77ecbd79`. Reproduced notice-span SHA-256: `51c4ad50b44cce86987c7fb58b65c7c1e89c5982a863a50d83a06199c28f881c`.

```text
/*!
 * express
 * Copyright(c) 2009-2013 TJ Holowaychuk
 * Copyright(c) 2013 Roman Shtylman
 * Copyright(c) 2014-2015 Douglas Christopher Wilson
 * MIT Licensed
 */
```

### Zod traversal-pattern attribution

Source: [packages/zod/src/v4/core/visit.ts](https://github.com/colinhacks/zod/blob/b500a2ee95134f81d74da7911d7378dd2853d042/packages/zod/src/v4/core/visit.ts). Full source SHA-256: `04108723a4a821de6f420a6adfe8e5dbca08e68bf30e7d9b079fab23d701ad69`. Reproduced notice-span SHA-256: `c84c401001a9acbbeb31094b4d00b4b5188bd69ac4b5dd155f213a9157b9e7aa`.

```text
// Traversal pattern adapted from Jaen's v3 `mapOnSchema` (Apache-2.0): https://gist.github.com/jaens/7e15ae1984bb338c86eb5e452dee3010
```

The Zod comment is reproduced exactly from the pinned source. Its linked external gist is not part of this corpus; the comment does not change the repository-level Zod license above.
