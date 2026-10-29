"""
提供访问应用数据的功能，如获取元素、访问全局变量、获取资源文件等功能

```python
# 获取名称为Button的选择器
package.selector('Button')
# 获取名称为Img的图像选择器
package.image_selector('Img')
# 以文本形式获取名称为names.txt的资源文件
package.resources.get_text('names.txt')
# 以二进制形式获取名称为users.xlsx的资源文件
package.resources.get_bytes('users.xlsx')
# 设置全局变量
package.variables['g_var1'] = 123
# 获取全局变量
package.variables['g_var1']
```
"""

from xbot.selector import SelectorStore, ImageSelectorStore
from xbot.primitives import VariableDict, ResourceReader, _sdmodules

import os


_path = os.path.dirname(__file__)
_sdmodules[_path] = globals()
_selector_store = SelectorStore(_path)
_image_selector_store = ImageSelectorStore(_path)


def selector(name):
    """
    从元素库中获取指定名称的选择器
    * @param name, 元素名称
    * @return `Selector`、`TableSelector`, 返回选择器对象
    """
    return _selector_store(name)

def image_selector(name):
    """
    从图像库中获取指定名称的图像选择器
    * @param name, 元素名称
    * @return `ImageSelector`, 返回图像选择器对象
    """
    return _image_selector_store(name)

resources = ResourceReader(__loader__, _path)
variables = VariableDict()
